-- Stock Watch used to fire RFQs by itself. Now it writes a recommendation the
-- buyer has to approve, like every other engine, so it needs its own engine
-- value on analysis_runs.

ALTER TYPE analysis_engine ADD VALUE IF NOT EXISTS 'daily';
