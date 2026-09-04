/**
 * Rule selection is a development-only affordance. It is hard-disabled in the
 * app so no environment variable — baked in at build time or injected at
 * runtime — can surface the sidebar in a deployment. The app always runs the
 * full rule set. To re-enable it for local development, temporarily set this to
 * `true` (or restore the previous env-driven flag).
 */
export const showRulesSidebar = false;
