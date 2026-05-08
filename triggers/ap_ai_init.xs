// =============================================================================
// ap_ai_init.xs — stable Player 12 AI entry point shared by all scenarios.
// =============================================================================
//
// Why this file exists:
//   AoMR scenario files reference an AI script by filename.  Re-pointing
//   every scenario to a new AI file each time we change AP logic would be
//   tedious.  Instead, every scenario's editor selects this file
//   (ap_ai_init.xs) for Player 12, and we keep its content tiny — just an
//   include for the live runtime.
//
//   GameClient.py (`generate_ap_ai_xs`) rewrites ap_ai_runtime.xs on every
//   AP client connect with the current trap state, hint configuration, etc.
//   Because *this* file never changes, scenario files don't need updating.
//
// Editor configuration: every scenario must set Player 12's AI to
// `ap_ai_init.xs`.  See aom/client/ApClient.py::_install_trigger_files for
// the destination path under <user_folder>/Game/AI/.
// =============================================================================

include "ap_ai_runtime.xs";
