from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, Dict, List, Sequence, Tuple

import plotly.graph_objects as go
from plotly.colors import qualitative


@dataclass
class TimeSeriesBuffer:
    maxlen: int = 2000

    def __post_init__(self) -> None:
        self._data: Dict[str, Deque[Tuple[float, float]]] = defaultdict(lambda: deque(maxlen=self.maxlen))

    def add(self, signal_fqn: str, ts: float, value: float) -> None:
        self._data[signal_fqn].append((ts, value))

    def cleanup_unused_signals(self, keep_signals: List[str]) -> None:
        """Remove data for signals not in the keep list to free memory."""
        all_signals = list(self._data.keys())
        for sig in all_signals:
            if sig not in keep_signals:
                del self._data[sig]

    def snapshot(self, selected_signals: Sequence[str], max_points: int | None = None) -> List["PlotSeries"]:
        """Capture the latest data for selected signals as PlotSeries objects."""
        series: List[PlotSeries] = []
        for sig in selected_signals:
            points = self._data.get(sig)
            if not points:
                continue

            if max_points is not None and len(points) > max_points:
                slice_points = list(points)[-max_points:]
            else:
                slice_points = list(points)

            timestamps = [ts for ts, _ in slice_points]
            values = [val for _, val in slice_points]
            if not timestamps:
                continue
            series.append(PlotSeries(name=sig, timestamps=timestamps, values=values))
        return series


@dataclass
class PlotSeries:
    name: str
    timestamps: List[float]
    values: List[float]


def build_signal_plot(series_list: Sequence[PlotSeries]) -> go.Figure:
    """Create an interactive Plotly figure for the selected signals."""
    fig = go.Figure()
    if not series_list:
        return fig

    palette = qualitative.Dark24
    for idx, series in enumerate(series_list):
        color = palette[idx % len(palette)]
        times = [datetime.fromtimestamp(ts) for ts in series.timestamps]
        fig.add_trace(
            go.Scatter(
                x=times,
                y=series.values,
                mode="lines+markers",
                name=series.name,
                line=dict(color=color, width=2),
                marker=dict(size=5, symbol="circle"),
                hovertemplate="Signal: %s<br>Time: %%{x|%%H:%%M:%%S.%%L}<br>Value: %%{y}<extra></extra>"
                % series.name,
                connectgaps=False,
            )
        )

    fig.update_layout(
        template="plotly_white",
        margin=dict(l=20, r=20, t=30, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
        hovermode="x unified",
        xaxis_title="Time",
        yaxis_title="Value",
    )
    fig.update_xaxes(showgrid=True)
    fig.update_yaxes(showgrid=True)
    return fig
