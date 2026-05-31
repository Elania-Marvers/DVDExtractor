export const POLL_INTERVAL_MS = 2000;
export const HEARTBEAT_MS = 1000;
export const MAX_STREAM_LINES = 240;
export const HEARTBEAT_NODE_IDS = {
  dot: "hb-dot",
  state: "hb-state",
  details: "hb-details",
};
export const LOCAL_STORAGE_KEYS = { mode: "dvd_mode" };

export const STATUS_CLASS = {
  completed: "ok",
  failed: "fail",
  running: "run",
  starting: "wait",
  queued: "wait",
  cancelling: "canc",
  cancelled: "canc",
};

export const STATUS_LABEL = {
  completed: "Terminé",
  failed: "Échec",
  running: "En cours",
  starting: "Démarrage",
  queued: "Attente",
  cancelling: "Annulation",
  cancelled: "Annulé",
};
