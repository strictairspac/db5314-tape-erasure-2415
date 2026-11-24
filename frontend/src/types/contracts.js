/** @typedef {{id:number, title:string, payload:object, author:string}} BusinessCase */
/** @typedef {{id:number, case_id:number, status:'queued'|'running'|'complete'|'failed'}} Run */
/** @typedef {{id:number, run_id:number, digest:string, report:{summary:string, metrics:object, sections:Array}}} Evidence */
/** @typedef {{id:number, contract_id:string, asset_id:string, hold_until:string|null, effective_from:string, effective_to:string|null, status:'accepted'|'conflict', conflict_with:number|null, created_at:string}} Amendment */
/** @typedef {{id:number, source_run_id:number, result_run_id:number, cutoff:string, request_key:string, created_at:string, result:Run}} Recompute */
/** @typedef {{run:Run, evidence_id:number|null, cutoff:string, contracts:string[], assets:Array<{asset_id:string, kind:string, object_key:string, reason:string, reason_code:string, contracts:string[]}>}} Retention */
