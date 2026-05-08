// =============================================================================
// ap_init.xs — single include line each scenario references.
// =============================================================================
//
// Why this exists:
//   AoMR scenarios pull in XS triggers via the editor's "Trigger Imports"
//   field, which by convention holds a single filename.  Pointing every
//   scenario at this stub means we can change the actual trigger code in
//   archipelago.xs (or split it across multiple files later) without
//   editing every campaign scenario.
//
// archipelago.xs in turn includes the auto-generated `aom_state.xs`, which
// is regenerated on every received-items event by GameClient.py.  See the
// header in archipelago.xs for the runtime architecture.
// =============================================================================

include "archipelago.xs";