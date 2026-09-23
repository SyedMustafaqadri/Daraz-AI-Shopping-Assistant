# Daraz 3CX Gemini voice agent

This integration adapts the official [3CX Agentic Call Control example](https://github.com/3cx/agentic-call-control) for this Daraz backend. 3CX carries the phone call and audio; Gemini Live handles the conversation; product tools call this project's validated FastAPI endpoints.

## Requirements

- 3CX V20 Update 10+ with Call Control API access enabled for the Service Principal.
- Node.js 20.12 or later, Corepack, and the project Python environment managed by `uv`.
- `GOOGLE_API_KEY`, `THREECX_APP_SECRET`, and Firecrawl credentials in the project-root `.env`.
- A 3CX extension for the first internal test. An assigned DID and SIP trunk are needed to call from a mobile/SIM number.

## Configure

1. Copy `.env.example` to `.env` at the repository root if you have not already done so.
2. In `.env`, set `THREECX_APP_SECRET` to the secret shown when you created the `darazassistant` Service Principal. Keep it private.
3. Confirm the other 3CX settings in `.env`: `THREECX_APP_ID=darazassistant` and `THREECX_PBX_BASE_URL=https://smartalkenttrial.3cx.com.au`.
4. Make a local config file from the example:

   ```powershell
   Copy-Item integrations/3cx-agentic-call-control/examples/gemini-realtime/config.yaml.example integrations/3cx-agentic-call-control/examples/gemini-realtime/config.yaml
   ```

   The config file is ignored by Git. Credentials are read from the root `.env`.

## Run and call

1. In one terminal at the repository root, start the Daraz API:

   ```powershell
   uv run uvicorn daraz_ai_shopping_assistant.main:app --reload --host 127.0.0.1 --port 8000
   ```

2. In a second terminal, install and start the 3CX service:

   ```powershell
   Set-Location integrations/3cx-agentic-call-control
   corepack yarn install
   corepack yarn start:gemini
   ```

3. From a registered 3CX extension, dial `darazassistant`. Try searching for a product and asking for details about one of the results.
4. Once the internal test works, assign your inbound DID to the `darazassistant` Service Principal in 3CX and confirm the SIP trunk inbound rule routes that DID to it. Call the DID from your SIM.

The agent exposes `search_products` and `get_product`. Search and detail results come from the existing validated backend. The agent cannot place orders or verify real-time stock.

## Troubleshooting

- **Missing credentials:** check the root `.env`; restart the 3CX service after edits.
- **Product tools fail:** keep the FastAPI server running at `http://127.0.0.1:8000` or change `DARAZ_API_BASE_URL` to the backend's reachable URL.
- **3CX login fails:** verify the Service Principal secret, PBX FQDN, Call Control API permission, and subscription entitlement.
- **Internal test works but SIM call does not:** check that the DID is assigned to the Service Principal and that the carrier trunk forwards inbound calls to that DID in 3CX.
