export const workspaceSignupEnabled = process.env.WORKSPACE_SIGNUP_ENABLED === "true";
export const workspaceRegistrationEnabled = process.env.WORKSPACE_REGISTRATION_ENABLED !== "false";
export const workspaceInviteSignupEnabled = process.env.WORKSPACE_INVITE_SIGNUP_ENABLED === "true";
export type WorkspaceMode = "mock" | "hospitable_read_only";
export const workspaceMode: WorkspaceMode = process.env.HOSPITABLE_READ_ONLY === "true" ? "hospitable_read_only" : "mock";
export const sessionTokenCookie = workspaceSignupEnabled ? "manager-session" : workspaceMode === "hospitable_read_only" ? "hospitable-local-token" : "cleaning-dev-token";
export const sessionRoleCookie = workspaceSignupEnabled ? "manager-role" : workspaceMode === "hospitable_read_only" ? "hospitable-local-role" : "cleaning-dev-role";
