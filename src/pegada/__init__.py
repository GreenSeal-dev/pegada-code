"""pegada — estimate the energy and carbon footprint of AI agent usage from token counts.

The core package is agent-agnostic: parsers turn agent transcripts into
:class:`~pegada.records.UsageRecord` objects, the estimator converts tokens into
energy and emissions intervals, and the ledger stores token counts (never
estimates) so results can be recomputed whenever coefficients are recalibrated.
"""

__version__ = "0.2.1"
