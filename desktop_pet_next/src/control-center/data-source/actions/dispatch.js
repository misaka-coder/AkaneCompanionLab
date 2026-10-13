import { CONTROL_CENTER_ACTIONS } from "../../action-router.js";
import { runChatBackendAction } from "./jobs.js";
import { runMcpBackendAction, runProviderBackendAction, runWorkflowBackendAction } from "./capabilities.js";
import { runPluginBackendAction } from "./plugins.js";
import { runApprovalPolicyBackendAction, runApprovalRequestBackendAction } from "./approval.js";
import { runQqBackendAction } from "./qq.js";

const providerBackendActionIds = new Set([
  CONTROL_CENTER_ACTIONS.abilitiesProviderConfigSave,
  CONTROL_CENTER_ACTIONS.abilitiesProviderHealthCheck,
  CONTROL_CENTER_ACTIONS.abilitiesProviderTtsTest,
  CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileInspectFolder,
  CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileSave
]);

const workflowBackendActionIds = new Set([
  CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigSave,
  CONTROL_CENTER_ACTIONS.abilitiesWorkflowFileImport,
  CONTROL_CENTER_ACTIONS.abilitiesWorkflowValidate
]);

const mcpBackendActionIds = new Set([
  CONTROL_CENTER_ACTIONS.abilitiesMcpConfigSave,
  CONTROL_CENTER_ACTIONS.abilitiesMcpDiscover,
  CONTROL_CENTER_ACTIONS.abilitiesMcpEnable,
  CONTROL_CENTER_ACTIONS.abilitiesMcpDisable,
  CONTROL_CENTER_ACTIONS.abilitiesMcpRestart,
  CONTROL_CENTER_ACTIONS.abilitiesMcpRemove
]);

const chatBackendActionIds = new Set([
  CONTROL_CENTER_ACTIONS.chatJobControl
]);

const pluginBackendActionIds = new Set([
  CONTROL_CENTER_ACTIONS.abilitiesPluginEnable,
  CONTROL_CENTER_ACTIONS.abilitiesPluginDisable,
  CONTROL_CENTER_ACTIONS.abilitiesPluginRollback,
  CONTROL_CENTER_ACTIONS.abilitiesPluginStageSource,
  CONTROL_CENTER_ACTIONS.abilitiesPluginStageWheel,
  CONTROL_CENTER_ACTIONS.abilitiesPluginStageMarket,
  CONTROL_CENTER_ACTIONS.abilitiesPluginInstall,
  CONTROL_CENTER_ACTIONS.abilitiesPluginDiscardStage,
  CONTROL_CENTER_ACTIONS.abilitiesPluginUninstall,
  CONTROL_CENTER_ACTIONS.abilitiesPluginConnectionSave
]);

const approvalPolicyBackendActionIds = new Set([
  CONTROL_CENTER_ACTIONS.abilitiesApprovalPolicySave
]);

const approvalRequestBackendActionIds = new Set([
  CONTROL_CENTER_ACTIONS.abilitiesApprovalRequestDecide
]);

const qqBackendActionIds = new Set([
  CONTROL_CENTER_ACTIONS.abilitiesQqSelfCheck
]);

export function backendActionRunner(actionId) {
  if (chatBackendActionIds.has(actionId)) return runChatBackendAction;
  if (providerBackendActionIds.has(actionId)) return runProviderBackendAction;
  if (workflowBackendActionIds.has(actionId)) return runWorkflowBackendAction;
  if (mcpBackendActionIds.has(actionId)) return runMcpBackendAction;
  if (pluginBackendActionIds.has(actionId)) return runPluginBackendAction;
  if (approvalRequestBackendActionIds.has(actionId)) return runApprovalRequestBackendAction;
  if (qqBackendActionIds.has(actionId)) return runQqBackendAction;
  if (approvalPolicyBackendActionIds.has(actionId)) return runApprovalPolicyBackendAction;
  return null;
}
