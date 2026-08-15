# Prowlarr aggregate adapter for SearXNG

This service gives SearXNG one Torznab engine while Prowlarr searches every enabled indexer. It does not return Prowlarr download URLs, so the Prowlarr API key is not leaked in results.

## Install

1. Copy this folder to `/opt/agent-stack/prowlarr-torznab-adapter`.
2. Add `compose.snippet.yaml`'s service under the existing `services:` block. Do not paste a second `services:` heading.
3. Add these values to `/opt/agent-stack/.env`:

   ```text
   PROWLARR_API_KEY=YOUR_RAW_PROWLARR_KEY
   ADAPTER_API_KEY=GENERATE_A_DIFFERENT_KEY
   ```

   Generate the adapter key with `openssl rand -hex 32`. Do not reuse the Prowlarr key.
4. Add the engine from `searxng.snippet.yml` to the existing settings. Replace its adapter key. Keep only one `engines:` heading.
5. Validate and start:

   ```bash
   cd /opt/agent-stack
   docker compose config
   docker compose up -d --build prowlarr-adapter
   docker compose restart searxng
   ```

## Test before removing old engines

```bash
docker exec agent-searxng sh -c 'wget -qO- "http://prowlarr-adapter:8080/health"'

curl -sG \
  --data-urlencode 'q=!pa Ubuntu' \
  --data-urlencode 'format=json' \
  http://192.168.90.129:8080/search | \
jq '{count: (.results | length), results: [.results[0:5][] | {title, engine, seed, leech}], errors: .unresponsive_engines}'
```

Once this succeeds, delete the old `p1` through `p48` SearXNG engine blocks and restart SearXNG. Indexers are then managed only in Prowlarr. New enabled Prowlarr indexers need no SearXNG configuration.

Use `!pa search words` to search the aggregate engine or select the **torrents** tab/category.
