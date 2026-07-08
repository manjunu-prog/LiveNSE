CREATE TABLE IF NOT EXISTS public.option_chain_snapshots (
    snapshot_ts timestamptz NOT NULL,
    snapshot_minute text NOT NULL,
    symbol text NOT NULL,
    strike integer NOT NULL,
    option_type text NOT NULL CHECK (option_type IN ('CE', 'PE')),
    ltp double precision,
    ltp_change_pct double precision,
    volume double precision,
    oi double precision,
    oi_change_pct double precision,
    oi_change double precision,
    iv double precision,
    PRIMARY KEY (snapshot_minute, symbol, strike, option_type)
);

CREATE INDEX IF NOT EXISTS idx_option_chain_snapshots_lookup
    ON public.option_chain_snapshots(symbol, strike, option_type, snapshot_ts DESC);

