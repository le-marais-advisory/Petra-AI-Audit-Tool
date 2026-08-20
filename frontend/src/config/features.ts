import { readBooleanEnv } from "@/config/runtime";

/**
 * Rule selection is a development affordance: client-facing deployments hide the
 * sidebar and run the full rule set. Defaults to hidden so an environment that
 * never sets the flag cannot expose it by omission; local dev opts in via
 * frontend/.env (see .env.example) or docker-compose.yml.
 */
export const showRulesSidebar = readBooleanEnv("VITE_SHOW_RULES_SIDEBAR", false);
