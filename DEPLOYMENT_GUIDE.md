# Deployment Guide

This guide explains how Petra Vision is deployed to Azure and how environment variables are applied during that process.

The deployment uses:

- `az` for Azure authentication and account context
- `azd` for environment management, infrastructure provisioning, image build/push, and app deployment
- `Bicep` through `azd` for Azure resources
- Microsoft Entra automation through `scripts/entra/sync_apps.py`

## What gets deployed

The deployment creates:

- `1` Azure Container Apps environment
- `1` Azure Container Registry
- `1` Log Analytics workspace
- `1` Container App for the backend API
- `1` Container App for the frontend
- Microsoft Entra app registrations for:
  - the frontend SPA
  - the protected backend API

## Prerequisites

Before deploying, make sure all of the following are true:

- You have `az` installed.
- You have `azd` installed.
- You have already authenticated with Azure.
- You have access to an Azure subscription where you can create resources.
- Your Microsoft Entra tenant allows your signed-in user to create app registrations.
- Docker is available if local container build is needed.

This project is configured with `remoteBuild: true` for both services, so Azure can build the images remotely instead of relying on local Docker. Even so, having Docker available locally is still useful for local testing.

## Important files

- `azure.yaml`
- `infra/main.bicep`
- `infra/main.parameters.json`
- `scripts/entra/sync_apps.py`
- `scripts/hooks/preprovision.sh`
- `scripts/hooks/postprovision.sh`

## How Azure env vars work in this project

This is the part that matters most for maintenance.

The source of truth for Azure deployment values is the active `azd` environment:

- normal values are stored in `.azure/<environment-name>/.env`
- secret values are stored through `azd env set-secret`
- `infra/main.parameters.json` maps those values into Bicep parameters
- `infra/main.bicep` maps those parameters into Azure Container App environment variables

The effective flow is:

1. `azd env set ...` or `azd env set-secret ...`
2. `azd hooks run preprovision` if Entra-related values need to be refreshed
3. `azd provision` to apply infrastructure and Container App configuration
4. `azd deploy` to put the current application images back on the apps

Important clarifications:

- seeing a value in `azd env get-values` only means it is stored in the active `azd` environment
- Azure does not receive that value until you run `azd provision` or `azd up`
- in this repository, `azd provision` updates Container App configuration but the real app images are restored by `azd deploy`
- if you want the least ambiguous command, use `azd up`

## What `azure.yaml` does

`azure.yaml` is still important, but its role is narrow:

- it tells `azd` which services exist
- it tells `azd` how to package and deploy those services
- it points `azd` at the `infra` folder and Bicep module

It is not the main file that carries your runtime env vars into Azure for this project.

That bridge is `infra/main.parameters.json`.

## Frontend runtime config in Azure

The frontend does not depend on baked-in production `VITE_*` values.

In Azure:

- Bicep assigns environment variables to the frontend Container App
- the container startup script writes those values into `runtime-config.js`
- the browser reads `runtime-config.js` at runtime

This allows the same frontend image to be reused across environments without rebuilding for every tenant or endpoint.

## High-level deployment flow

When you run `azd up`, this repository does the following:

1. Runs the `preprovision` hook.
2. Creates or updates the Microsoft Entra app registrations.
3. Stores the generated tenant/app identifiers in the active `azd` environment.
4. Provisions Azure resources with Bicep.
5. Builds and pushes the container images to Azure Container Registry.
6. Deploys the backend and frontend to Azure Container Apps.
7. Runs the `postprovision` hook.
8. Adds the deployed frontend URL as a redirect URI in Microsoft Entra.

## Step 1: Sign in

Authenticate with Azure first.

```bash
az login
azd auth login
```

If you use multiple subscriptions, confirm the correct one is active:

```bash
az account show
```

If needed, switch subscription:

```bash
az account set --subscription "<subscription-id>"
```

## Step 2: Create an azd environment

Create an environment name for this deployment.

Example:

```bash
azd env new personal
```

This creates the local `azd` environment metadata for the deployment.

## Step 3: Set environment values

At minimum, set the Azure region, auth mode, provider selection, and the API key you intend to use.

For secrets such as provider API keys, use `azd env set-secret` instead of `azd env set`.

This is the recommended approach because:

- the secret value does not need to be passed directly as a shell argument
- it avoids leaving the raw key in shell history
- it avoids storing the raw key in plain text in the local `azd` environment file

Example using OpenAI:

```bash
azd env set AZURE_LOCATION eastus
azd env set AUTH_ENABLED true
azd env set TEXT_PROVIDER openai
azd env set VISION_PROVIDER openai
azd env set OPENAI_TEXT_MODEL gpt-5.4-mini
azd env set OPENAI_VISION_MODEL gpt-5.4
azd env set-secret OPENAI_API_KEY
```

Example using Claude:

```bash
azd env set AZURE_LOCATION eastus
azd env set AUTH_ENABLED true
azd env set TEXT_PROVIDER claude
azd env set VISION_PROVIDER claude
azd env set CLAUDE_TEXT_MODEL claude-sonnet-5
azd env set CLAUDE_VISION_MODEL claude-sonnet-5
azd env set-secret ANTHROPIC_API_KEY
```

When `azd env set-secret` runs, `azd` prompts for the value securely and stores a protected secret reference instead of the raw secret value.

You can inspect the current environment values with:

```bash
azd env get-values
```

### Values you should normally set directly

- `AZURE_LOCATION`
- `AUTH_ENABLED`
- `TEXT_PROVIDER`
- `VISION_PROVIDER`
- `OPENAI_*`
- `CLAUDE_*`
- provider API keys with `azd env set-secret`

### Values that are normally derived automatically

The Entra hook updates these values for you:

- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID`
- `AZURE_FRONTEND_CLIENT_ID`
- `AZURE_AUDIENCE`
- `AZURE_ALLOWED_CLIENT_APP_IDS`
- `VITE_AZURE_*`
- `VITE_API_SCOPE`

Do not maintain a separate committed `.env.production` file for Azure deployment values. The supported source of truth is the active `azd` environment.

## Step 4: Prepare Microsoft Entra app registrations

Before running `azd provision --preview` or `azd up`, prepare the Microsoft Entra app registrations:

```bash
azd hooks run preprovision
```

This step creates or updates the frontend and backend app registrations and stores the generated identifiers in the active `azd` environment.

You can confirm the values were populated with:

```bash
azd env get-values
```

Look for:

- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID`
- `AZURE_FRONTEND_CLIENT_ID`

## Step 5: Optional preview before provisioning

After the `preprovision` hook has populated the required Entra values, you can preview the infrastructure changes:

```bash
azd provision --preview
```

This is useful if you want to inspect the resources before anything is created.

## Step 6: Deploy everything

For the safest full flow, run:

```bash
azd up
```

This is the main end-to-end deployment command.

## What happens during deployment

### Microsoft Entra setup

The `preprovision` hook runs `scripts/entra/sync_apps.py`.

That script:

- creates or updates the backend API app registration
- creates or updates the frontend SPA app registration
- configures the API scope `access_as_user`
- creates the service principals if needed
- tries to assign the signed-in user as owner
- stores the generated IDs in the active `azd` environment

The `postprovision` hook then adds the deployed frontend URL as a redirect URI.

### Azure resources

The infrastructure defined in `infra/main.bicep` provisions:

- Azure Container Registry
- Log Analytics Workspace
- Azure Container Apps Environment
- Backend Container App
- Frontend Container App
- `AcrPull` permissions for the apps

### Application deployment

After provisioning:

- the backend image is built and pushed to ACR
- the frontend image is built and pushed to ACR
- both Container Apps are updated to use the pushed images

Important:

- `azd provision` applies infrastructure and Container App configuration
- `azd deploy` applies the current application images
- for this repository, changing env vars should be followed by `azd deploy`, or you can simply use `azd up`

## Step 7: Verify the deployment

After `azd up` completes:

```bash
azd show
```

You should get the deployed application resources and environment.

You can also inspect the environment values:

```bash
azd env get-values
```

Look for:

- `FRONTEND_ENDPOINT`
- `BACKEND_ENDPOINT`
- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID`
- `AZURE_FRONTEND_CLIENT_ID`

Expected result:

- the frontend URL loads publicly
- the frontend redirects to Microsoft sign-in
- a successful sign-in can request an access token for the backend
- the backend accepts tokens issued for the configured tenant and API scope

## Re-deploying after changes

Use the following rules depending on what changed.

### Case 1: You changed only application code

Examples:

- Python source files
- React source files
- Dockerfiles
- static frontend assets

Run:

```bash
azd deploy
```

### Case 2: You changed environment variables or secrets

Examples:

- `TEXT_PROVIDER`
- `VISION_PROVIDER`
- `AUTH_ENABLED`
- `OPENAI_*`
- `CLAUDE_*`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

Run:

```bash
azd hooks run preprovision
azd provision
azd deploy
```

Why both commands are needed:

- `azd provision` applies the new environment variables to Azure resources
- `azd deploy` restores the real application images after the infrastructure update

### Case 3: You changed infrastructure or deployment wiring

Examples:

- `infra/main.bicep`
- `infra/main.parameters.json`
- `azure.yaml`
- deployment hooks
- Entra registration flow

Run:

```bash
azd hooks run preprovision
azd provision
azd deploy
```

### Case 4: You want the safe full flow every time

Run:

```bash
azd up
```

This is the least ambiguous option.

## Recommended command matrix

```bash
# Inspect current environment values
azd env get-values

# Full safe deployment
azd up

# Code only
azd deploy

# Env vars, secrets, infra, auth wiring
azd hooks run preprovision
azd provision
azd deploy
```

## Verify what Azure actually received

Check the active `azd` environment:

```bash
azd env get-values
```

Check the deployed backend environment variables:

```bash
az containerapp show -g <resource-group> -n petra-api --query "properties.template.containers[0].env" -o table
```

Check the deployed frontend environment variables:

```bash
az containerapp show -g <resource-group> -n petra-frontend --query "properties.template.containers[0].env" -o table
```

Check the frontend runtime config served to browsers:

```bash
curl -sS https://<frontend-endpoint>/runtime-config.js
```

## Common failure scenarios

### Microsoft Entra permissions error

Symptoms:

- the deployment fails in `preprovision`
- the script cannot create applications
- the script cannot assign owners

Meaning:

- the signed-in principal does not have enough permissions in the Microsoft Entra tenant

Resolution:

- use an account with permission to register applications
- or create the app registrations manually and then set the required IDs in `azd env`

### Consent-related issue after deploy

Symptoms:

- sign-in works but API access fails
- consent or permissions errors appear in the browser or backend

Meaning:

- tenant policy requires admin consent for the delegated API permission

Resolution:

- grant admin consent in Microsoft Entra for the frontend app to call the backend API scope

### Resource deployment issue

Symptoms:

- provisioning fails in the Azure phase

Resolution:

- inspect the failure in the Azure Portal
- run `azd up` again after correcting the issue
- or clean up and restart from scratch using the rollback steps below

## Rollback and cleanup

Yes, the Azure deployment can be reverted.

### Option 1: Remove the Azure resources created by this environment

Run:

```bash
azd down -e personal
```

Replace `personal` with the environment name you created.

This deletes the Azure resources associated with that `azd` environment.

### Option 2: Remove the local azd environment metadata

After Azure resources are removed, you can also remove the local environment definition:

```bash
azd env remove -e personal
```

This removes the local `azd` environment state. It does not delete Azure resources by itself.

### Important limitation: Microsoft Entra app registrations are not removed by azd down

The Microsoft Entra applications created by the hooks are not part of the Azure resource group deployment.

That means:

- `azd down` removes the Azure resources
- `azd down` does not remove the Entra app registrations

If you want a full cleanup, you must also delete manually from Microsoft Entra:

- the frontend SPA app registration
- the backend API app registration

## Full cleanup flow

If you want to fully remove the deployment and start over:

1. Delete Azure resources:

```bash
azd down -e personal
```

2. Remove the local `azd` environment:

```bash
azd env remove -e personal
```

3. Delete the Microsoft Entra app registrations manually in the tenant.

## Safe usage notes

- Use a dedicated `azd` environment per deployment target.
- Do not manually add unrelated resources into the same resource group if you want clean rollback behavior.
- Treat Microsoft Entra cleanup as a separate step from Azure cleanup.

## Minimal command summary

```bash
az login
azd auth login
azd env new personal
azd env set AZURE_LOCATION eastus
azd env set AUTH_ENABLED true
azd env set TEXT_PROVIDER claude
azd env set VISION_PROVIDER claude
azd env set CLAUDE_TEXT_MODEL claude-sonnet-5
azd env set CLAUDE_VISION_MODEL claude-sonnet-5
azd env set-secret ANTHROPIC_API_KEY
azd hooks run preprovision
azd provision --preview
azd provision
azd deploy
azd show
```

## Minimal rollback summary

```bash
azd down -e personal
azd env remove -e personal
```

Then manually remove the Microsoft Entra app registrations if you want complete cleanup.
