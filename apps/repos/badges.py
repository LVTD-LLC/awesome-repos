from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape as xml_escape

from django.utils import timezone

if TYPE_CHECKING:
    from apps.repos.models import Repository


BADGE_WIDTH = 640
BADGE_HEIGHT = 200
BADGE_HISTORY_LIMIT = 60
CHART_X = 300
CHART_Y = 56
CHART_WIDTH = 296
CHART_HEIGHT = 88
FONT_FAMILY = "Inter, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"


@dataclass(frozen=True)
class BadgeMetric:
    key: str
    value_attr: str
    snapshot_attr: str
    singular_label: str
    plural_label: str
    title: str


BADGE_METRICS = {
    "stars": BadgeMetric(
        key="stars",
        value_attr="stars",
        snapshot_attr="stars",
        singular_label="star",
        plural_label="stars",
        title="Star history",
    ),
    "commits": BadgeMetric(
        key="commits",
        value_attr="commit_count",
        snapshot_attr="commit_count",
        singular_label="commit",
        plural_label="commits",
        title="Commit history",
    ),
}

BADGE_THEMES = {
    "light": {
        "background": "#ffffff",
        "border": "#dbe5d3",
        "text": "#0f172a",
        "muted": "#475569",
        "soft": "#f8fafc",
        "grid": "#e2e8f0",
        "accent": "#15803d",
        "accent_soft": "#dcfce7",
        "accent_text": "#166534",
    },
    "dark": {
        "background": "#020617",
        "border": "#1e293b",
        "text": "#f8fafc",
        "muted": "#cbd5e1",
        "soft": "#0f172a",
        "grid": "#1e293b",
        "accent": "#22c55e",
        "accent_soft": "#052e16",
        "accent_text": "#bbf7d0",
    },
}


def repository_badge_svg(
    repository: Repository,
    *,
    metric: str = "stars",
    theme: str = "light",
    variant: str = "history",
    days: int | None = None,
) -> str:
    metric_config = BADGE_METRICS.get(metric, BADGE_METRICS["stars"])
    colors = BADGE_THEMES.get(theme, BADGE_THEMES["light"])
    if variant == "growth":
        return _repository_growth_badge_svg(
            repository,
            metric_config,
            colors=colors,
            days=days or 7,
        )

    return _repository_history_badge_svg(repository, metric_config, colors=colors)


def _repository_history_badge_svg(
    repository: Repository,
    metric_config: BadgeMetric,
    *,
    colors: dict[str, str],
) -> str:
    history_values, snapshot_count = _metric_history_values(repository, metric_config)
    current_value = _current_metric_value(repository, metric_config)
    current_label = _format_compact_number(current_value)
    metric_label = _metric_label(metric_config, current_value)

    minimum = min(history_values)
    maximum = max(history_values)
    range_label = _format_range(minimum, maximum)
    history_label = (
        f"{snapshot_count} {_pluralize('capture', snapshot_count)}"
        if snapshot_count
        else "current catalog snapshot"
    )
    full_name = _truncate_text(repository.full_name, 34)
    awesome_list_count = _awesome_list_count(repository)
    title = f"Awesome badge for {repository.full_name}"
    description = (
        f"{repository.full_name} has {_format_full_number(current_value)} {metric_label} "
        f"and appears in {awesome_list_count} awesome {_pluralize('list', awesome_list_count)}."
    )

    return _render_badge_svg(
        colors=colors,
        full_name=full_name,
        title=title,
        description=description,
        current_label=current_label,
        metric_label=metric_label,
        secondary_parts=_repository_secondary_parts(repository),
        chart_title=metric_config.title,
        footer_left=history_label,
        footer_right=range_label,
        history_values=history_values,
    )


def _repository_growth_badge_svg(
    repository: Repository,
    metric_config: BadgeMetric,
    *,
    colors: dict[str, str],
    days: int,
) -> str:
    period_days = 30 if days == 30 else 7
    current_value = _current_metric_value(repository, metric_config)
    baseline_snapshot = _period_baseline_snapshot(repository, metric_config, period_days)
    baseline_value = (
        int(getattr(baseline_snapshot, metric_config.snapshot_attr))
        if baseline_snapshot is not None
        else None
    )
    delta = current_value - baseline_value if baseline_value is not None else None
    full_name = _truncate_text(repository.full_name, 34)
    growth_name = "star growth" if metric_config.key == "stars" else "commit velocity"
    metric_label = f"{metric_config.plural_label} in last {period_days} days"
    chart_title = f"{period_days}-day {growth_name}"

    if delta is None:
        current_label = "n/a"
        footer_left = "needs baseline capture"
        footer_right = _format_full_number(current_value)
        history_values = [current_value]
        description_text = (
            f"{repository.full_name} does not have enough tracked history for "
            f"{period_days}-day {growth_name}."
        )
    else:
        current_label = _format_signed_compact_number(delta)
        footer_left = f"baseline {baseline_snapshot.captured_at:%Y-%m-%d}"
        footer_right = (
            f"{_format_full_number(baseline_value)} to {_format_full_number(current_value)}"
        )
        history_values = _period_metric_history_values(
            repository,
            metric_config,
            baseline_snapshot=baseline_snapshot,
            current_value=current_value,
        )
        description_text = (
            f"{repository.full_name} changed by {_format_full_number(delta)} "
            f"{metric_config.plural_label} over the last {period_days} days."
        )

    return _render_badge_svg(
        colors=colors,
        full_name=full_name,
        title=f"{chart_title} for {repository.full_name}",
        description=description_text,
        current_label=current_label,
        metric_label=metric_label,
        secondary_parts=_repository_secondary_parts(repository),
        chart_title=chart_title,
        footer_left=footer_left,
        footer_right=footer_right,
        history_values=history_values,
    )


def _render_badge_svg(
    *,
    colors: dict[str, str],
    full_name: str,
    title: str,
    description: str,
    current_label: str,
    metric_label: str,
    secondary_parts: list[str],
    chart_title: str,
    footer_left: str,
    footer_right: str,
    history_values: list[int],
) -> str:
    points = _sparkline_points(history_values)
    line_path = _sparkline_path_from_points(points)
    area_path = _sparkline_area_path(line_path)
    last_x, last_y = points[-1]
    mid_y = CHART_Y + CHART_HEIGHT / 2
    chart_right = CHART_X + CHART_WIDTH

    background = colors["background"]
    border = colors["border"]
    text = colors["text"]
    muted = colors["muted"]
    soft = colors["soft"]
    grid = colors["grid"]
    accent = colors["accent"]
    accent_soft = colors["accent_soft"]
    accent_text = colors["accent_text"]

    svg_lines = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{BADGE_WIDTH}" '
            f'height="{BADGE_HEIGHT}" viewBox="0 0 {BADGE_WIDTH} {BADGE_HEIGHT}" '
            'role="img" aria-labelledby="title desc">'
        ),
        f'  <title id="title">{_escape_text(title)}</title>',
        f'  <desc id="desc">{_escape_text(description)}</desc>',
        f'  <rect width="{BADGE_WIDTH}" height="{BADGE_HEIGHT}" rx="20" fill="{background}" />',
        (
            f'  <rect x="0.5" y="0.5" width="{BADGE_WIDTH - 1}" '
            f'height="{BADGE_HEIGHT - 1}" rx="19.5" fill="none" stroke="{border}" />'
        ),
        f'  <g font-family="{FONT_FAMILY}">',
        f'    <rect x="28" y="24" width="88" height="28" rx="14" fill="{accent_soft}" />',
        (
            '    <text x="72" y="43" text-anchor="middle" font-size="13" '
            f'font-weight="700" fill="{accent_text}">Awesome</text>'
        ),
        (
            '    <text x="28" y="80" font-size="25" font-weight="800" '
            f'fill="{text}">{_escape_text(full_name)}</text>'
        ),
        (
            '    <text x="28" y="112" font-size="37" font-weight="800" '
            f'fill="{text}">{_escape_text(current_label)}</text>'
        ),
        (
            '    <text x="28" y="138" font-size="15" font-weight="700" '
            f'fill="{muted}">{_escape_text(metric_label)}</text>'
        ),
        (
            '    <text x="28" y="168" font-size="13" font-weight="600" '
            f'fill="{muted}">{_escape_text(" - ".join(secondary_parts))}</text>'
        ),
        (
            f'    <text x="{CHART_X}" y="34" font-size="14" font-weight="800" '
            f'fill="{text}">{_escape_text(chart_title)}</text>'
        ),
        (
            f'    <text x="{CHART_X}" y="160" font-size="12" font-weight="600" '
            f'fill="{muted}">{_escape_text(footer_left)}</text>'
        ),
        (
            f'    <text x="{chart_right}" y="160" text-anchor="end" font-size="12" '
            f'font-weight="600" fill="{muted}">{_escape_text(footer_right)}</text>'
        ),
        (
            f'    <rect x="{CHART_X}" y="{CHART_Y}" width="{CHART_WIDTH}" '
            f'height="{CHART_HEIGHT}" rx="12" fill="{soft}" />'
        ),
        (
            f'    <path d="M {CHART_X} {mid_y:.1f} H {chart_right}" fill="none" '
            f'stroke="{grid}" stroke-width="1" />'
        ),
        f'    <path d="{area_path}" fill="{accent_soft}" opacity="0.75" />',
        (
            f'    <path d="{line_path}" fill="none" stroke="{accent}" '
            'stroke-width="4" stroke-linecap="round" stroke-linejoin="round" />'
        ),
        f'    <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="5" fill="{accent}" />',
        "  </g>",
        "</svg>",
        "",
    ]
    return "\n".join(svg_lines)


def _repository_secondary_parts(repository: Repository) -> list[str]:
    awesome_list_count = _awesome_list_count(repository)
    secondary_parts = [f"{awesome_list_count} awesome {_pluralize('list', awesome_list_count)}"]
    if repository.language:
        secondary_parts.append(repository.language)
    if repository.is_archived:
        secondary_parts.append("archived")
    return secondary_parts


def _metric_history_values(repository: Repository, metric: BadgeMetric) -> tuple[list[int], int]:
    snapshots = list(
        repository.snapshots.order_by("-captured_at", "-id").only(
            "stars",
            "commit_count",
            "captured_at",
        )[:BADGE_HISTORY_LIMIT]
    )
    snapshot_count = len(snapshots)
    values = [
        int(value)
        for snapshot in reversed(snapshots)
        if (value := getattr(snapshot, metric.snapshot_attr)) is not None
    ]
    current_value = _current_metric_value(repository, metric)
    if not values:
        return [current_value], snapshot_count
    if values[-1] != current_value:
        values.append(current_value)
    return values, snapshot_count


def _period_baseline_snapshot(repository: Repository, metric: BadgeMetric, days: int):
    cutoff = timezone.now() - timedelta(days=days)
    return (
        repository.snapshots.filter(
            captured_at__lte=cutoff,
            **{f"{metric.snapshot_attr}__isnull": False},
        )
        .order_by("-captured_at", "-id")
        .only("captured_at", "stars", "commit_count")
        .first()
    )


def _period_metric_history_values(
    repository: Repository,
    metric: BadgeMetric,
    *,
    baseline_snapshot,
    current_value: int,
) -> list[int]:
    snapshots = list(
        repository.snapshots.filter(
            captured_at__gte=baseline_snapshot.captured_at,
            **{f"{metric.snapshot_attr}__isnull": False},
        )
        .order_by("captured_at", "id")
        .only("captured_at", "stars", "commit_count")[:BADGE_HISTORY_LIMIT]
    )
    values = [int(getattr(snapshot, metric.snapshot_attr)) for snapshot in snapshots]
    baseline_value = int(getattr(baseline_snapshot, metric.snapshot_attr))
    if not values or values[0] != baseline_value:
        values.insert(0, baseline_value)
    if values[-1] != current_value:
        values.append(current_value)
    return values


def _current_metric_value(repository: Repository, metric: BadgeMetric) -> int:
    return int(getattr(repository, metric.value_attr) or 0)


def _awesome_list_count(repository: Repository) -> int:
    annotated_count = getattr(repository, "awesome_count", None)
    if annotated_count is not None:
        return int(annotated_count)
    return repository.awesome_items.count()


def _metric_label(metric: BadgeMetric, value: int) -> str:
    return metric.singular_label if value == 1 else metric.plural_label


def _pluralize(label: str, value: int) -> str:
    return label if value == 1 else f"{label}s"


def _format_compact_number(value: int) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"{_format_compact_decimal(value / 1_000_000)}M"
    if absolute >= 1_000:
        return f"{_format_compact_decimal(value / 1_000)}k"
    return str(value)


def _format_signed_compact_number(value: int) -> str:
    if value > 0:
        return f"+{_format_compact_number(value)}"
    return _format_compact_number(value)


def _format_compact_decimal(value: float) -> str:
    rounded = round(value, 1)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:.1f}"


def _format_full_number(value: int) -> str:
    return f"{value:,}"


def _format_range(minimum: int, maximum: int) -> str:
    if minimum == maximum:
        return _format_full_number(maximum)
    return f"{_format_full_number(minimum)} to {_format_full_number(maximum)}"


def _sparkline_path_from_points(points: list[tuple[float, float]]) -> str:
    first_x, first_y = points[0]
    commands = [f"M {first_x:.1f} {first_y:.1f}"]
    commands.extend(f"L {x:.1f} {y:.1f}" for x, y in points[1:])
    return " ".join(commands)


def _sparkline_area_path(line_path: str) -> str:
    return (
        f"{line_path} L {CHART_X + CHART_WIDTH:.1f} {CHART_Y + CHART_HEIGHT:.1f} "
        f"L {CHART_X:.1f} {CHART_Y + CHART_HEIGHT:.1f} Z"
    )


def _sparkline_points(values: list[int]) -> list[tuple[float, float]]:
    if len(values) == 1:
        return [
            (float(CHART_X), CHART_Y + CHART_HEIGHT / 2),
            (float(CHART_X + CHART_WIDTH), CHART_Y + CHART_HEIGHT / 2),
        ]

    minimum = min(values)
    maximum = max(values)
    spread = maximum - minimum
    points = []
    for index, value in enumerate(values):
        x = CHART_X + (CHART_WIDTH * index / (len(values) - 1))
        if spread == 0:
            y = CHART_Y + CHART_HEIGHT / 2
        else:
            y = CHART_Y + CHART_HEIGHT - (CHART_HEIGHT * (value - minimum) / spread)
        points.append((x, y))
    return points


def _escape_text(value: str) -> str:
    return xml_escape(str(value), {'"': "&quot;", "'": "&apos;"})


def _truncate_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3]}..."
