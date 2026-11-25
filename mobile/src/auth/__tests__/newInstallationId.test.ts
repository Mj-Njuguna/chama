import { INSTALLATION_ID } from '../deviceIdentity';
import { newInstallationId } from '../newInstallationId';

describe('newInstallationId', () => {
  it('produces a v4 UUID that matches the contract format', () => {
    for (let i = 0; i < 50; i++) {
      const id = newInstallationId();
      expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
      expect(id).toMatch(INSTALLATION_ID);
    }
  });
});
