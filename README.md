# FYERS Option Chain Desk

Standalone Streamlit app for live FYERS option-chain tracking.

## What it does

- Pulls live quotes and option-chain data from FYERS
- Highlights OI change % and volume pressure
- Tags strikes such as `CE Buying More` and `PUT Selling More`
- Stores minute snapshots in Supabase when configured, otherwise locally in SQLite
- Lets you click a strike and inspect its 1-minute history
- Shows index and strike-level OB/FVG context from FYERS candle history

## Run

```bash
streamlit run /Users/apple/fyers_option_chain_desk/app.py
```

## Supabase Setup

Run `supabase_schema.sql` in your Supabase SQL editor. The app uses the `option_chain_snapshots` table for 1-minute strike history.

Set these in Streamlit secrets or environment variables:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` or `SUPABASE_KEY`
- `SUPABASE_TABLE` optional, defaults to `option_chain_snapshots`

The app falls back to `option_chain_history.sqlite3` when Supabase is not configured.

## Credentials

Set these in your environment or a local `.env` file:

- `FYERS_FY_ID`
- `FYERS_APP_ID`
- `FYERS_APP_SECRET`
- `FYERS_REDIRECT_URI`
- `FYERS_PIN`
- `FYERS_TOTP_KEY`

For deployment, use Streamlit secrets. See `.streamlit/secrets.example.toml` for the expected structure.

The app also falls back to `~/Desktop/OptionTerminal/.streamlit/secrets.toml` if present.

## Deploy

1. Push this folder to a GitHub repository.
2. Create a Supabase project and run `supabase_schema.sql`.
3. Deploy the repo on Streamlit Community Cloud or another Streamlit host.
4. Add FYERS and Supabase secrets from `.streamlit/secrets.example.toml`.
5. Set the app entrypoint to `app.py`.

Do not commit `.env`, `.streamlit/secrets.toml`, logs, or the SQLite database.
