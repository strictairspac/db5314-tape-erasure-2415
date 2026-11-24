export const state = {session: JSON.parse(sessionStorage.getItem('demo-session') || 'null'), spec:null,
                      cases:[], runs:[], evidence:[], audit:[], amendments:[], recomputations:[],
                      retention:null, draft:null, amendmentKey:null, amendmentSignature:null};

export function setSession(session) {
  state.session = session;
  state.spec = null;
  state.draft = null;
  state.retention = null;
  state.amendmentKey = null;
  state.amendmentSignature = null;
  if (session) sessionStorage.setItem('demo-session', JSON.stringify(session));
  else sessionStorage.removeItem('demo-session');
}
