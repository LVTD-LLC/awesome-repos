const CHART_SELECTOR = "[data-repository-history-chart]";

document.addEventListener("DOMContentLoaded", () => {
  if (window.d3) {
    initRepositoryHistoryCharts();
    return;
  }

  window.addEventListener("load", () => initRepositoryHistoryCharts(), { once: true });
});

export function initRepositoryHistoryCharts(root = document) {
  const charts = [...root.querySelectorAll(CHART_SELECTOR)];
  if (!charts.length || !window.d3) {
    return;
  }

  charts.forEach((chart) => {
    const data = historyData(chart.dataset.historySource);
    const renderer = () => renderChart(chart, data);
    renderer();

    if (window.ResizeObserver) {
      const observer = new ResizeObserver(renderer);
      observer.observe(chart);
    } else {
      window.addEventListener("resize", renderer);
    }
  });

  observeThemeChanges(() => charts.forEach((chart) => renderChart(chart, historyData(chart.dataset.historySource))));
}

function historyData(sourceId) {
  const source = document.getElementById(sourceId);
  if (!source) {
    return [];
  }

  try {
    return JSON.parse(source.textContent);
  } catch {
    return [];
  }
}

function renderChart(chart, rawData) {
  const d3 = window.d3;
  const plot = chart.querySelector("[data-chart-plot]");
  const metric = chart.dataset.metric;
  const label = chart.dataset.label || "Repository history";
  if (!plot || !metric) {
    return;
  }

  const data = rawData
    .map((point) => ({
      date: new Date(point.captured_at),
      value: point[metric] == null ? null : Number(point[metric]),
    }))
    .filter((point) => Number.isFinite(point.date.getTime()) && Number.isFinite(point.value))
    .sort((left, right) => left.date - right.date);

  plot.innerHTML = "";
  plot.classList.add("relative");
  if (!data.length) {
    plot.append(emptyState("No tracked data yet."));
    return;
  }

  const width = Math.max(plot.clientWidth, 320);
  const height = Math.max(plot.clientHeight, 240);
  const margin = { top: 18, right: 18, bottom: 34, left: 54 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  const theme = chartTheme(metric);

  const svg = d3
    .select(plot)
    .append("svg")
    .attr("role", "img")
    .attr("aria-label", `${label} history`)
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("width", "100%")
    .attr("height", "100%");

  const x = d3.scaleTime().domain(expandedDateDomain(d3.extent(data, (point) => point.date))).range([0, innerWidth]);
  const y = d3.scaleLinear().domain(expandedValueDomain(d3.extent(data, (point) => point.value))).nice().range([innerHeight, 0]);
  const line = d3
    .line()
    .x((point) => x(point.date))
    .y((point) => y(point.value))
    .curve(d3.curveMonotoneX);

  const content = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);
  content
    .append("g")
    .attr("class", "grid")
    .call(d3.axisLeft(y).ticks(4).tickSize(-innerWidth).tickFormat(""))
    .call((axis) => axis.select(".domain").remove())
    .call((axis) => axis.selectAll("line").attr("stroke", theme.grid));

  content
    .append("path")
    .datum(data)
    .attr("fill", "none")
    .attr("stroke", theme.line)
    .attr("stroke-width", 2.5)
    .attr("stroke-linecap", "round")
    .attr("stroke-linejoin", "round")
    .attr("d", line);

  content
    .selectAll("circle")
    .data(data)
    .join("circle")
    .attr("cx", (point) => x(point.date))
    .attr("cy", (point) => y(point.value))
    .attr("r", data.length === 1 ? 4 : 3)
    .attr("fill", theme.line)
    .attr("stroke", theme.surface)
    .attr("stroke-width", 1.5);

  content
    .append("g")
    .attr("transform", `translate(0,${innerHeight})`)
    .call(d3.axisBottom(x).ticks(4).tickSizeOuter(0).tickFormat(d3.timeFormat("%b %d")))
    .call(styleAxis, theme);

  content
    .append("g")
    .call(d3.axisLeft(y).ticks(4).tickSizeOuter(0).tickFormat(d3.format("~s")))
    .call(styleAxis, theme);

  attachTooltip({
    content,
    data,
    height: innerHeight,
    label,
    margin,
    plot,
    theme,
    width: innerWidth,
    x,
    y,
  });
}

function attachTooltip({ content, data, height, label, margin, plot, theme, width, x, y }) {
  const d3 = window.d3;
  const bisect = d3.bisector((point) => point.date).center;
  const marker = content.append("g").attr("display", "none");
  marker.append("line").attr("y1", 0).attr("y2", height).attr("stroke", theme.hoverLine).attr("stroke-dasharray", "3 3");
  marker.append("circle").attr("r", 4).attr("fill", theme.line).attr("stroke", theme.surface).attr("stroke-width", 2);

  const tooltip = d3
    .select(plot)
    .append("div")
    .attr("class", "pointer-events-none absolute z-10 hidden rounded-lg border px-3 py-2 text-xs shadow-lg")
    .style("background", theme.surface)
    .style("border-color", theme.border)
    .style("color", theme.text);

  content
    .append("rect")
    .attr("width", width)
    .attr("height", height)
    .attr("fill", "transparent")
    .on("pointerenter", () => {
      marker.attr("display", null);
      tooltip.classed("hidden", false);
    })
    .on("pointerleave", () => {
      marker.attr("display", "none");
      tooltip.classed("hidden", true);
    })
    .on("pointermove", (event) => {
      const [pointerX] = d3.pointer(event);
      const point = data[bisect(data, x.invert(pointerX))];
      if (!point) {
        return;
      }

      const markerX = x(point.date);
      const markerY = y(point.value);
      marker.attr("transform", `translate(${markerX},0)`);
      marker.select("circle").attr("cy", markerY);
      const tooltipLeft = Math.max(8, Math.min(margin.left + markerX + 12, plot.clientWidth - 160));
      const tooltipTop = Math.max(8, margin.top + markerY - 46);
      tooltip
        .html(`<div class="font-semibold">${formatNumber(point.value)} ${label.toLowerCase()}</div><div>${formatDate(point.date)}</div>`)
        .style("left", `${tooltipLeft}px`)
        .style("top", `${tooltipTop}px`);
    });
}

function chartTheme(metric) {
  const dark = document.documentElement.classList.contains("dark");
  const line = metric === "stars" ? "#15803d" : "#2563eb";
  return {
    border: dark ? "#334155" : "#e2e8f0",
    grid: dark ? "#1e293b" : "#e2e8f0",
    hoverLine: dark ? "#64748b" : "#94a3b8",
    line,
    muted: dark ? "#94a3b8" : "#64748b",
    surface: dark ? "#020617" : "#ffffff",
    text: dark ? "#e2e8f0" : "#0f172a",
  };
}

function styleAxis(axis, theme) {
  axis.select(".domain").attr("stroke", theme.border);
  axis.selectAll("line").attr("stroke", theme.border);
  axis.selectAll("text").attr("fill", theme.muted).attr("font-size", 11);
}

function expandedDateDomain([min, max]) {
  if (!min || !max) {
    return [new Date(), new Date()];
  }
  if (min.getTime() !== max.getTime()) {
    return [min, max];
  }

  const oneDay = 24 * 60 * 60 * 1000;
  return [new Date(min.getTime() - oneDay), new Date(max.getTime() + oneDay)];
}

function expandedValueDomain([min, max]) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return [0, 1];
  }
  if (min === max) {
    return [Math.max(0, min - 1), max + 1];
  }

  return [Math.max(0, min), max];
}

function observeThemeChanges(callback) {
  if (!window.MutationObserver) {
    return;
  }

  const observer = new MutationObserver(callback);
  observer.observe(document.documentElement, {
    attributeFilter: ["class"],
    attributes: true,
  });
}

function emptyState(message) {
  const element = document.createElement("div");
  element.className = "flex h-full items-center justify-center rounded-xl bg-gray-50 text-sm text-gray-500 dark:bg-gray-900 dark:text-gray-400";
  element.textContent = message;
  return element;
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(value);
}

function formatDate(value) {
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(value);
}
