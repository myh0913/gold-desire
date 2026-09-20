"""首批内置因子：导入即注册（每个模块一组因子）。

模块与因子对应关系：

- :mod:`amplitude` — ``first_yin_amplitude``
- :mod:`shape` — ``first_yin_shape``
- :mod:`volume_ratio` — ``next_day_vol_vs_first_yin`` / ``vol_vs_wave_peak`` /
  ``vol_vs_prev`` / ``vol_vs_wave_mean``
- :mod:`open_pct` — ``next_day_open_pct`` / ``first_yin_open_pct`` / ``next_next_day_open_pct``
- :mod:`boards` — ``continue_boards``
- :mod:`wave_trend` — ``wave_vol_trend``
- :mod:`low_time` — ``first_yin_low_time``
- :mod:`close_pos` — ``first_yin_close_pos``
- :mod:`one_word` — ``wave_one_word_count``
- :mod:`next_day_close` — ``next_day_close_pct``
"""

from app.factors.builtin import (
    amplitude,
    boards,
    close_pos,
    low_time,
    next_day_close,
    one_word,
    open_pct,
    shape,
    volume_ratio,
    wave_trend,
)

__all__ = [
    "amplitude",
    "boards",
    "close_pos",
    "low_time",
    "next_day_close",
    "one_word",
    "open_pct",
    "shape",
    "volume_ratio",
    "wave_trend",
]
