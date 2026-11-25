import { createContext, type ReactNode, useContext } from 'react';

import type { AppServices } from './appServices';

const AppServicesContext = createContext<AppServices | null>(null);

export function AppServicesProvider({ services, children }: { services: AppServices; children: ReactNode }) {
  return <AppServicesContext.Provider value={services}>{children}</AppServicesContext.Provider>;
}

export function useAppServices(): AppServices {
  const value = useContext(AppServicesContext);
  if (value === null) throw new Error('useAppServices outside AppServicesProvider');
  return value;
}
