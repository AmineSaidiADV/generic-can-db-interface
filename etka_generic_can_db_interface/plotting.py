from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Tuple

import pandas as pd


@dataclass
class TimeSeriesBuffer:
    maxlen: int = 2000

    def __post_init__(self) -> None:
        self._data: Dict[str, Deque[Tuple[float, float]]] = defaultdict(lambda: deque(maxlen=self.maxlen))

    def add(self, signal_fqn: str, ts: float, value: float) -> None:
        self._data[signal_fqn].append((ts, value))

    def as_dataframe(self, selected_signals: List[str]) -> pd.DataFrame:
        # Build a wide dataframe indexed by time
        series: Dict[str, pd.Series] = {}
        for sig in selected_signals:
            points = list(self._data.get(sig, []))
            if not points:
                continue
            s = pd.Series(data=[v for (_, v) in points], index=[t for (t, _) in points], name=sig)
            series[sig] = s
        if not series:
            return pd.DataFrame()
        df = pd.concat(series.values(), axis=1)
        df.index.name = "time"
        return df
