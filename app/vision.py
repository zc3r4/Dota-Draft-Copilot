from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import cv2
import numpy as np
import requests

from .config import CACHE_DIR, Rect

STEAM_CDN = "https://cdn.cloudflare.steamstatic.com"
USER_AGENT = "DotaDraftCopilot/1.0 (+local desktop helper)"

WIDE_SIZE = (128, 72)
SQUARE_SIZE = (96, 96)
# Translation slack for matchTemplate.  Calibrated boxes are rarely pixel
# perfect, so the slot is padded before matching.  At the template scale this
# 36 px border absorbs roughly a 9% offset of a 200 px card, which is the
# accuracy cliff for an evenly split strip.
PAD = 36
# Only this many best histogram candidates get the expensive template match.
# Colour histograms are translation tolerant and put the correct hero in the
# top 16 even when the strip is shifted by ~9% (measured on the real 127 hero
# portraits); 48 leaves a wide safety margin while cutting CPU by ~2.6x.
CANDIDATE_LIMIT = 48


@dataclass
class TemplateVariant:
    gray: np.ndarray
    edges: np.ndarray
    hist: np.ndarray
    size: tuple[int, int]


@dataclass
class HeroTemplate:
    hero_id: int
    name: str
    variants: dict[str, TemplateVariant]


@dataclass
class HeroMatch:
    hero_id: int
    score: float


@dataclass
class SlotResult:
    slot: int
    hero_id: int | None
    score: float
    margin: float
    top: list[HeroMatch]
    empty: bool = False


def _clahe(gray: np.ndarray) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    return clahe.apply(gray)


def _gradient(gray: np.ndarray) -> np.ndarray:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    mag = cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX)
    return mag.astype(np.uint8)


def _histogram(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [24, 24], [0, 180, 0, 256])
    hist = hist.astype(np.float32).ravel()
    norm = float(np.linalg.norm(hist))
    return hist / norm if norm > 1e-6 else hist


def _features(bgr: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    resized = cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)
    gray = _clahe(cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY))
    edges = _gradient(gray)
    hist = _histogram(resized)
    return gray, edges, hist


def _read_image(path: Path) -> np.ndarray | None:
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def hero_image_url(hero: dict) -> str | None:
    path = hero.get("img") or hero.get("icon")
    if not path:
        # OpenDota's /heroes response stopped including images in 2026; the
        # same data is present in /heroStats. As a last resort derive the
        # Steam CDN slug from npc_dota_hero_<slug>.
        name = str(hero.get("name") or "")
        prefix = "npc_dota_hero_"
        if name.startswith(prefix) and len(name) > len(prefix):
            path = f"/apps/dota2/images/dota_react/heroes/{name[len(prefix):]}.png"
    if not path:
        return None
    return STEAM_CDN + str(path).split("?")[0]


def item_image_url(item: dict) -> str | None:
    path = item.get("img")
    if not path:
        return None
    return STEAM_CDN + str(path).split("?")[0]


def _fit_aspect(bgr: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Center-crop to the template aspect ratio, then resize.

    Calibration errors and different pick-screen layouts produce cells that
    are not exactly 16:9. Stretching them distorted the features; cropping the
    excess keeps the portrait geometry intact.
    """
    target_w, target_h = size
    target_ratio = target_w / max(1, target_h)
    h, w = bgr.shape[:2]
    if w <= 0 or h <= 0:
        return cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)
    if w / h > target_ratio:
        crop_w = max(1, int(round(h * target_ratio)))
        x = max(0, (w - crop_w) // 2)
        bgr = bgr[:, x : x + crop_w]
    elif w / h < target_ratio:
        crop_h = max(1, int(round(w / target_ratio)))
        y = max(0, (h - crop_h) // 2)
        bgr = bgr[y : y + crop_h, :]
    return cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)


class TemplateStore:
    """Downloads hero portraits and provides fast template matching."""

    def __init__(self, cache_dir: Path = CACHE_DIR / "heroes") -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.templates: dict[int, HeroTemplate] = {}
        self.hero_names: dict[int, str] = {}
        self.failed_downloads: list[int] = []
        self._wide_matrix: np.ndarray | None = None
        self._wide_order: list[int] = []
        self._square_matrix: np.ndarray | None = None
        self._square_order: list[int] = []

    def download(self, heroes: Iterable[dict], progress: Callable[[int, int], None] | None = None) -> None:
        heroes = list(heroes)
        total = len(heroes)
        done = 0
        todo: list[tuple[dict, Path, str]] = []
        for hero in heroes:
            hid = int(hero.get("id") or 0)
            if hid <= 0:
                continue
            path = self.cache_dir / f"{hid}.png"
            if path.exists() and path.stat().st_size > 1000:
                done += 1
                continue
            url = hero_image_url(hero)
            if url:
                todo.append((hero, path, url))
            else:
                self.failed_downloads.append(hid)
        if progress:
            progress(done, total)

        def fetch(item: tuple[dict, Path, str]) -> int:
            hero, path, url = item
            try:
                response = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
                response.raise_for_status()
                if len(response.content) < 500:
                    raise ValueError("too small")
                path.write_bytes(response.content)
                return int(hero.get("id") or 0)
            except Exception:
                return 0

        if todo:
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(fetch, item) for item in todo]
                for future in as_completed(futures):
                    hid = future.result()
                    done += 1
                    if hid == 0:
                        self.failed_downloads.append(0)
                    if progress:
                        progress(min(done, total), total)

    def load(
        self,
        heroes: Iterable[dict],
        progress: Callable[[int, int], None] | None = None,
        allow_download: bool = True,
    ) -> None:
        heroes = list(heroes)
        if allow_download:
            self.download(heroes, progress)
        self.templates.clear()
        self.hero_names.clear()

        total = len(heroes)
        for index, hero in enumerate(heroes):
            hid = int(hero.get("id") or 0)
            name = str(hero.get("localized_name") or hero.get("name") or hid)
            if hid <= 0:
                continue
            self.hero_names[hid] = name
            img = _read_image(self.cache_dir / f"{hid}.png")
            if img is None:
                self.failed_downloads.append(hid)
                continue
            variants: dict[str, TemplateVariant] = {}
            try:
                gray, edges, hist = _features(img, WIDE_SIZE)
                variants["wide"] = TemplateVariant(gray, edges, hist, WIDE_SIZE)
                h, w = img.shape[:2]
                side = min(h, w)
                top = (h - side) // 2
                left = (w - side) // 2
                square = img[top : top + side, left : left + side]
                gray, edges, hist = _features(square, SQUARE_SIZE)
                variants["square"] = TemplateVariant(gray, edges, hist, SQUARE_SIZE)
            except Exception:
                continue
            self.templates[hid] = HeroTemplate(hid, name, variants)
            if progress and (index % 10 == 0 or index == total - 1):
                progress(index + 1, total)

        self._build_matrices()

    def _build_matrices(self) -> None:
        for key, attr in (("wide", "_wide"), ("square", "_square")):
            ids: list[int] = []
            rows: list[np.ndarray] = []
            for hid, template in self.templates.items():
                variant = template.variants.get(key)
                if variant is not None:
                    ids.append(hid)
                    rows.append(variant.hist)
            if rows:
                matrix = np.vstack(rows).astype(np.float32)
                matrix.setflags(write=False)
            else:
                matrix = None
            setattr(self, f"{attr}_matrix", matrix)
            setattr(self, f"{attr}_order", ids)

    def _match_variant(self, bgr: np.ndarray, key: str) -> list[HeroMatch]:
        ids = self._wide_order if key == "wide" else self._square_order
        matrix = self._wide_matrix if key == "wide" else self._square_matrix
        if not ids or matrix is None:
            return []

        # Resize the slot to the template size first, then add a small replicated
        # border.  Scaling the slot up to size+PAD (the old behaviour) changed
        # the portrait scale and cost accuracy; the border only gives
        # matchTemplate a few pixels of translation slack.
        sample_size = WIDE_SIZE if key == "wide" else SQUARE_SIZE
        base = _fit_aspect(bgr, sample_size)
        pad_before = PAD // 2
        pad_after = PAD - pad_before
        padded = cv2.copyMakeBorder(
            base,
            pad_before,
            pad_after,
            pad_before,
            pad_after,
            cv2.BORDER_REPLICATE,
        )
        gray = _clahe(cv2.cvtColor(padded, cv2.COLOR_BGR2GRAY))
        edges = _gradient(gray)
        hist = _histogram(base)

        hist_scores = matrix @ hist
        if len(ids) > CANDIDATE_LIMIT:
            # np.argpartition keeps this O(n) instead of sorting 127 scores.
            keep = np.argpartition(-hist_scores, CANDIDATE_LIMIT)[:CANDIDATE_LIMIT]
        else:
            keep = np.arange(len(ids))
        results: list[HeroMatch] = []
        for idx in keep:
            hid = ids[int(idx)]
            template = self.templates[hid].variants[key]
            res_gray = cv2.matchTemplate(gray, template.gray, cv2.TM_CCOEFF_NORMED)
            res_edge = cv2.matchTemplate(edges, template.edges, cv2.TM_CCOEFF_NORMED)
            s_gray = float(np.nanmax(res_gray)) if res_gray.size else 0.0
            s_edge = float(np.nanmax(res_edge)) if res_edge.size else 0.0
            score = 0.55 * max(0.0, s_edge) + 0.28 * max(0.0, s_gray) + 0.17 * max(0.0, float(hist_scores[int(idx)]))
            results.append(HeroMatch(hid, score))
        results.sort(key=lambda m: m.score, reverse=True)
        return results

    def recognize(self, slot_bgr: np.ndarray) -> SlotResult:
        if slot_bgr is None or slot_bgr.size == 0 or min(slot_bgr.shape[:2]) < 12:
            return SlotResult(0, None, 0.0, 0.0, [], empty=True)

        gray = cv2.cvtColor(slot_bgr, cv2.COLOR_BGR2GRAY)
        flatness = float(np.std(gray))
        if flatness < 5.0:
            return SlotResult(0, None, 0.0, 0.0, [], empty=True)

        h, w = slot_bgr.shape[:2]
        # Pick-screen cards are 16:9 wide (the game draws 118x66 art). A box
        # that accidentally includes the player-name row is still wider than
        # tall (~1.1), so prefer the wide variant until the cell is almost
        # square. The square variant then serves as a rescue pass.
        primary = "wide" if (w / max(1, h)) >= 1.1 else "square"
        results = self._match_variant(slot_bgr, primary)

        def center_crop(frac: float) -> np.ndarray:
            ch = max(8, int(round(h * frac)))
            cw = max(8, int(round(w * frac)))
            y = max(0, (h - ch) // 2)
            x = max(0, (w - cw) // 2)
            return slot_bgr[y : y + ch, x : x + cw]

        def top_crop(frac: float) -> np.ndarray:
            return slot_bgr[: max(8, int(round(h * frac))), :]

        if not results:
            return SlotResult(0, None, 0.0, 0.0, [], empty=True)

        # A calibrated box may include a border or a generous margin around the
        # portrait. Retry with the centre of the slot, which makes the portrait
        # fill the template again. This must run even for weak first scores:
        # with a 10-12% margin the whole-slot match can drop to ~0.2, yet the
        # centred crop recognises the hero confidently.
        if 0.15 <= results[0].score < 0.72:
            for frac in (0.90, 0.80):
                candidate = self._match_variant(center_crop(frac), primary)
                if candidate and candidate[0].score > results[0].score:
                    results = candidate
                if results[0].score >= 0.72:
                    break

        if 0.12 <= results[0].score < 0.46:
            secondary = "square" if primary == "wide" else "wide"
            for frac in (1.0, 0.90, 0.80):
                source = slot_bgr if frac == 1.0 else center_crop(frac)
                candidate = self._match_variant(source, secondary)
                if candidate and candidate[0].score > results[0].score:
                    results = candidate

            # The portrait sits at the top of a pick card. If the calibrated
            # box also includes the player-name row, a centre crop keeps part
            # of that row and cuts the top of the art; crop from the top
            # instead. Only weak slots reach this branch.
            if results[0].score < 0.46:
                for frac in (0.82, 0.68, 0.58):
                    candidate = self._match_variant(top_crop(frac), "wide")
                    if candidate and candidate[0].score > results[0].score:
                        results = candidate

        top = results[0]
        second = results[1].score if len(results) > 1 else 0.0
        margin = top.score - second
        empty = (top.score < 0.30) or (flatness < 7.0 and top.score < 0.40)
        return SlotResult(
            slot=0,
            hero_id=None if empty else top.hero_id,
            score=top.score,
            margin=margin,
            top=results[:5],
            empty=empty,
        )


def split_team_box(box: tuple[int, int, int, int], count: int = 5, inset: float = 0.055) -> list[tuple[int, int, int, int]]:
    """Split a calibrated team strip into `count` portrait cells."""
    x, y, w, h = box
    cells: list[tuple[int, int, int, int]] = []
    if w >= h:
        cw = w / count
        for i in range(count):
            cells.append((int(round(x + i * cw)), int(y), int(round(cw)), int(h)))
    else:
        ch = h / count
        for i in range(count):
            cells.append((int(x), int(round(y + i * ch)), int(w), int(round(ch))))

    refined: list[tuple[int, int, int, int]] = []
    for cx, cy, cw, ch in cells:
        dx = int(round(cw * inset))
        dy = int(round(ch * inset))
        refined.append((cx + dx, cy + dy, max(4, cw - 2 * dx), max(4, ch - 2 * dy)))
    return refined


def classify_confidence(score: float) -> str:
    if score >= 0.62:
        return "high"
    if score >= 0.47:
        return "medium"
    return "low"


class DraftScanner:
    def __init__(self, store: TemplateStore) -> None:
        self.store = store

    def scan_side(self, frame: np.ndarray, rect: Rect) -> list[SlotResult]:
        fh, fw = frame.shape[:2]
        px = rect.to_px(fw, fh)
        cells = split_team_box(px)
        results: list[SlotResult] = []
        for index, (x, y, w, h) in enumerate(cells):
            crop = frame[max(0, y) : y + h, max(0, x) : x + w]
            result = self.store.recognize(crop)
            result.slot = index
            results.append(result)
        return results

    def scan(self, frame: np.ndarray, boxes: dict[str, Rect]) -> dict[str, list[SlotResult]]:
        return {side: self.scan_side(frame, box) for side, box in boxes.items() if box}


def draw_debug(frame: np.ndarray, results: dict[str, list[SlotResult]], boxes: dict[str, Rect]) -> np.ndarray:
    out = frame.copy()
    fh, fw = frame.shape[:2]
    colors = {"radiant": (80, 200, 80), "dire": (60, 60, 220)}
    for side, rect in boxes.items():
        x, y, w, h = rect.to_px(fw, fh)
        color = colors.get(side, (255, 255, 255))
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
        for result in results.get(side, []):
            cells = split_team_box((x, y, w, h))
            if result.slot < len(cells):
                cx, cy, cw, ch = cells[result.slot]
                cv2.rectangle(out, (cx, cy), (cx + cw, cy + ch), color, 1)
                label = f"{result.hero_id or '?'} {result.score:.2f}"
                cv2.putText(out, label, (cx + 4, cy + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return out
