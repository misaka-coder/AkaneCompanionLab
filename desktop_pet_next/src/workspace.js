import { createWorkspaceHost } from "./workspace/host.js";
import { createWorkspaceApplication } from "./workspace/application.js";
import "./workspace.css";

const workspace = createWorkspaceApplication({ host: createWorkspaceHost() });
void workspace.start();
