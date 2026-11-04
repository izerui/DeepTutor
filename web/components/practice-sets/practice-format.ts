/** A practice set's time (seconds since the epoch) in the reader's locale. */
export const formatDate = (timestamp: number | null | undefined) =>
  timestamp ? new Date(timestamp * 1000).toLocaleString() : "";
