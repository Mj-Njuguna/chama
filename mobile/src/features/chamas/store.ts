import { create } from 'zustand';

/** Which home an official sees: their own member view, or the group's official view. */
export type ViewMode = 'member' | 'official';

type ChamaUiState = {
  /** The session's active chama, as last confirmed by the server (login, /me or switch-chama). */
  readonly activeChamaId: string | null;
  readonly viewMode: ViewMode;
  setActiveChama(chamaId: string | null): void;
  setViewMode(mode: ViewMode): void;
  reset(): void;
};

/**
 * UI-only state. Never balances, roles or permissions: those come from the server on each read.
 * Not persisted: a cold start reads the active chama from /me again.
 */
export const useChamaUi = create<ChamaUiState>((set) => ({
  activeChamaId: null,
  viewMode: 'official',
  setActiveChama: (chamaId) => set({ activeChamaId: chamaId }),
  setViewMode: (mode) => set({ viewMode: mode }),
  reset: () => set({ activeChamaId: null, viewMode: 'official' }),
}));
