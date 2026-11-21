"""
Visual tokens regression: ensure no retired rose/scarlet colors remain in frontend.

The Wada 268 palette (olive #253122, sienna #bb7125, mizuiro #bce4e5, umenezu #b59392,
paper #f7f6f2) replaced the rose (#e54a6f/#e76c8f) and scarlet (#c5222e/#a61924/#b21f2d)
themes. This test fails if any forbidden hex or the old --primary variable appears in
frontend CSS or TSX files.
"""

import re
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

FORBIDDEN_HEXES = {
    "#e54a6f",  # rose primary
    "#e76c8f",  # rose secondary
    "#c5222e",  # scarlet primary
    "#a61924",  # scarlet secondary
    "#b21f2d",  # accent-ink
}

FORBIDDEN_PATTERNS = [
    re.compile(r"var\(--primary\)"),
    re.compile(r"var\(--secondary\)"),
    re.compile(r"var\(--accent-ink\)"),
    re.compile(r"var\(--accent-soft\)"),
]

COMMENT_PATTERN = re.compile(r"/\*.*?\*/|//.*?$", re.MULTILINE | re.DOTALL)


def strip_comments(content: str) -> str:
    """Remove CSS and JS comments from content."""
    return COMMENT_PATTERN.sub("", content)


def test_no_rose_or_scarlet_hex_in_frontend():
    """Frontend CSS and TSX files must not contain retired rose/scarlet hex values."""
    violations = []
    for ext in ("*.css", "*.tsx"):
        for path in FRONTEND_DIR.rglob(ext):
            if "node_modules" in str(path):
                continue
            content = path.read_text(encoding="utf-8")
            content_no_comments = strip_comments(content)
            content_lower = content_no_comments.lower()
            for forbidden in FORBIDDEN_HEXES:
                if forbidden.lower() in content_lower:
                    violations.append(f"{path.relative_to(FRONTEND_DIR)}: contains {forbidden}")
    assert not violations, "Forbidden rose/scarlet hex found:\n" + "\n".join(violations)


def test_no_old_primary_or_accent_variables_in_frontend():
    """Frontend CSS and TSX files must not use the retired --primary/--accent variables."""
    violations = []
    for ext in ("*.css", "*.tsx"):
        for path in FRONTEND_DIR.rglob(ext):
            if "node_modules" in str(path):
                continue
            content = path.read_text(encoding="utf-8")
            content_no_comments = strip_comments(content)
            for pattern in FORBIDDEN_PATTERNS:
                if pattern.search(content_no_comments):
                    violations.append(f"{path.relative_to(FRONTEND_DIR)}: contains {pattern.pattern}")
    assert not violations, "Forbidden variables found:\n" + "\n".join(violations)


def test_cta_contrast_ink_contrast_documented():
    """
    Verify that the tokens.css or globals.css documents the contrast ratios
    for the CTA and ink colors (non-functional, checks comment presence).
    """
    globals_css = FRONTEND_DIR / "app" / "globals.css"
    content = globals_css.read_text(encoding="utf-8")
    assert "12.60" in content or "12.601" in content, "Ink-on-paper contrast ratio (12.601) should be documented"
    assert "4.76" in content or "4.766" in content, "CTA-text-on-cta contrast ratio (4.766) should be documented"


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color to RGB tuple."""
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _relative_luminance(r: int, g: int, b: int) -> float:
    """Calculate relative luminance per WCAG 2.1."""

    def channel(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(hex1: str, hex2: str) -> float:
    """Calculate WCAG 2.1 contrast ratio between two hex colors."""
    l1 = _relative_luminance(*_hex_to_rgb(hex1))
    l2 = _relative_luminance(*_hex_to_rgb(hex2))
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


TOKEN_PATTERN = re.compile(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})")


def _parse_tokens(content: str, block_selector: str = ":root") -> dict[str, str]:
    """Parse CSS custom property hex values from a specific block in globals.css."""
    tokens: dict[str, str] = {}
    in_block = False
    brace_depth = 0
    for line in content.split("\n"):
        if block_selector in line and "{" in line:
            in_block = True
            brace_depth = 1
            continue
        if in_block:
            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0:
                break
            for match in TOKEN_PATTERN.finditer(line):
                tokens[f"--{match.group(1)}"] = match.group(2).lower()
    return tokens


def test_wada268_contrast_ratios():
    """
    Verify the Wada 268 palette color pairs meet WCAG 2.1 contrast requirements.
    Parses token values from globals.css :root block.
    - Ink on paper: >= 7:1 (AAA normal text)
    - CTA text on CTA: >= 4.5:1 (AA normal text)
    - Link on paper: >= 4.5:1 (AA normal text)
    - Line on paper: >= 3:1 (UI components)
    """
    globals_css = FRONTEND_DIR / "app" / "globals.css"
    content = globals_css.read_text(encoding="utf-8")
    tokens = _parse_tokens(content, ":root")

    olive = tokens.get("--olive", "#253122")
    paper = tokens.get("--paper", "#f7f6f2")
    cta = tokens.get("--cta", "#a56321")
    cta_text = tokens.get("--cta-text", "#ffffff")
    link = tokens.get("--link", "#834f1a")
    line = tokens.get("--line", "#6e7468")

    ink_on_paper = _contrast_ratio(olive, paper)
    assert ink_on_paper >= 7.0, f"Ink on paper {ink_on_paper:.3f} < 7:1 AAA (--olive {olive} on --paper {paper})"

    cta_text_on_cta = _contrast_ratio(cta_text, cta)
    assert cta_text_on_cta >= 4.5, (
        f"CTA text on CTA {cta_text_on_cta:.3f} < 4.5:1 AA (--cta-text {cta_text} on --cta {cta})"
    )

    link_on_paper = _contrast_ratio(link, paper)
    assert link_on_paper >= 4.5, f"Link on paper {link_on_paper:.3f} < 4.5:1 AA (--link {link} on --paper {paper})"

    line_on_paper = _contrast_ratio(line, paper)
    assert line_on_paper >= 3.0, f"Line on paper {line_on_paper:.3f} < 3:1 UI (--line {line} on --paper {paper})"


def test_dark_theme_contrast_ratios():
    """
    Verify dark theme color pairs meet WCAG 2.1 contrast requirements.
    Parses token values from globals.css [data-theme="dark"] block.
    - Ink on paper: >= 7:1 (AAA normal text)
    - CTA text on CTA: >= 4.5:1 (AA normal text)
    - Link on paper: >= 4.5:1 (AA normal text)
    """
    globals_css = FRONTEND_DIR / "app" / "globals.css"
    content = globals_css.read_text(encoding="utf-8")
    dark_tokens = _parse_tokens(content, '[data-theme="dark"]')
    root_tokens = _parse_tokens(content, ":root")

    olive = root_tokens.get("--olive", "#253122")
    dark_ink = dark_tokens.get("--ink", "#bce4e5")
    dark_paper = dark_tokens.get("--paper", olive)
    dark_cta = dark_tokens.get("--cta", "#c98d51")
    dark_cta_text = dark_tokens.get("--cta-text", olive)
    dark_link = dark_tokens.get("--link", "#d4a36a")

    ink_on_paper_dark = _contrast_ratio(dark_ink, dark_paper)
    assert ink_on_paper_dark >= 7.0, (
        f"Dark ink on paper {ink_on_paper_dark:.3f} < 7:1 AAA (--ink {dark_ink} on --paper {dark_paper})"
    )

    cta_text_on_cta_dark = _contrast_ratio(dark_cta_text, dark_cta)
    assert cta_text_on_cta_dark >= 4.5, (
        f"Dark CTA text on CTA {cta_text_on_cta_dark:.3f} < 4.5:1 AA (--cta-text {dark_cta_text} on --cta {dark_cta})"
    )

    link_on_paper_dark = _contrast_ratio(dark_link, dark_paper)
    assert link_on_paper_dark >= 4.5, (
        f"Dark link on paper {link_on_paper_dark:.3f} < 4.5:1 AA (--link {dark_link} on --paper {dark_paper})"
    )


def test_no_prefers_color_scheme_dark_changes_tokens():
    """
    Verify that prefers-color-scheme: dark media query does NOT change --paper or --ink.
    Light-only ships now; dark is deferred to V1b.
    """
    globals_css = FRONTEND_DIR / "app" / "globals.css"
    content = globals_css.read_text(encoding="utf-8")

    media_dark_pattern = re.compile(
        r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)\s*\{([^}]+)\}",
        re.IGNORECASE | re.DOTALL,
    )
    media_matches = media_dark_pattern.findall(content)

    forbidden_in_media = ["--paper", "--ink"]
    violations = []
    for block in media_matches:
        for token in forbidden_in_media:
            if token in block:
                violations.append(f"prefers-color-scheme:dark block sets {token}")

    assert not violations, (
        "prefers-color-scheme:dark must not change computed --paper or --ink (light-only ships now):\n"
        + "\n".join(violations)
    )


LOW_CONTRAST_COLOR_VARS = ["var(--umenezu)", "var(--sienna)"]
COLOR_PROPERTY_PATTERN = re.compile(r"\bcolor\s*:\s*([^;]+);", re.IGNORECASE)


def test_no_low_contrast_text_colors():
    """
    Verify that umenezu (#b59392, 2.78:1 on white at 11px) and sienna (#bb7125)
    are not used as `color:` values in globals.css. These tokens have insufficient
    contrast for text. Use --muted or --ink instead.
    """
    globals_css = FRONTEND_DIR / "app" / "globals.css"
    content = globals_css.read_text(encoding="utf-8")
    content_no_comments = strip_comments(content)

    violations = []
    for match in COLOR_PROPERTY_PATTERN.finditer(content_no_comments):
        value = match.group(1).strip()
        for forbidden in LOW_CONTRAST_COLOR_VARS:
            if forbidden in value:
                violations.append(f"color: {value} uses {forbidden}")

    assert not violations, (
        "Low-contrast tokens must not be used as text colors "
        "(umenezu 2.78:1, sienna <4.5:1 on white):\n" + "\n".join(violations)
    )
