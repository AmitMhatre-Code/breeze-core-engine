import type { QueryClient } from "@tanstack/react-query";

/** Invalidate shell queries that should reflect post-trade margin / book state. */
export function invalidateTradingShellQueries(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: ["home", "data"] });
  void queryClient.invalidateQueries({ queryKey: ["book"] });
  void queryClient.invalidateQueries({ queryKey: ["portfolio", "positions"] });
}
