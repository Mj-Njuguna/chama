import { fireEvent, render, screen } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { StepUpPrompt } from '../StepUpPrompt';

async function renderPrompt(onSubmit: (pin: string) => Promise<void>, onCancel = jest.fn()) {
  await render(
    <SafeAreaProvider
      initialMetrics={{
        frame: { x: 0, y: 0, width: 360, height: 640 },
        insets: { top: 0, left: 0, right: 0, bottom: 0 },
      }}
    >
      <StepUpPrompt onSubmit={onSubmit} onCancel={onCancel} />
    </SafeAreaProvider>,
  );
  return onCancel;
}

describe('StepUpPrompt (server PIN)', () => {
  it('says why it appears', async () => {
    await renderPrompt(jest.fn());
    expect(screen.getByText('To protect your account, enter your PIN.')).toBeOnTheScreen();
  });

  it('hands the six digits to the caller (the server verifies them) and clears the dots', async () => {
    const onSubmit = jest.fn(async () => undefined);
    await renderPrompt(onSubmit);
    for (const digit of '2759') await fireEvent.press(screen.getByRole('button', { name: digit }));
    await fireEvent.press(screen.getByRole('button', { name: 'Delete' }));
    expect(screen.getByLabelText('PIN, 3 of 6 digits entered')).toBeOnTheScreen();
    expect(onSubmit).not.toHaveBeenCalled();
    for (const digit of '931') await fireEvent.press(screen.getByRole('button', { name: digit }));
    expect(onSubmit).toHaveBeenCalledWith('275931');
    expect(await screen.findByLabelText('PIN, 0 of 6 digits entered')).toBeOnTheScreen();
    // The digits themselves are never on the screen.
    expect(screen.queryByText('275931')).toBeNull();
  });

  it('shows why it is asked when the caller says', async () => {
    await render(
      <SafeAreaProvider
        initialMetrics={{
          frame: { x: 0, y: 0, width: 360, height: 640 },
          insets: { top: 0, left: 0, right: 0, bottom: 0 },
        }}
      >
        <StepUpPrompt onSubmit={jest.fn()} onCancel={jest.fn()} reason="Needed to record a payment" />
      </SafeAreaProvider>,
    );
    expect(screen.getByText('Enter your PIN')).toBeOnTheScreen();
    expect(screen.getByText('Needed to record a payment')).toBeOnTheScreen();
  });

  it('can be cancelled', async () => {
    const onCancel = await renderPrompt(jest.fn());
    await fireEvent.press(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalled();
  });
});
