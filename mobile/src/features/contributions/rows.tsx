import { StyleSheet, Text, View } from 'react-native';

import { t } from '../../i18n';
import { formatKes } from '../../lib/money';
import { formatDate, formatDayMonth } from '../../lib/nairobiTime';
import { c } from '../app/theme';
import type { CashbookTransaction, Obligation, Payment, StatementEntry } from '../m2/m2Api';

/** One list row: a title, a muted detail line and an amount on the right. */
export function LineItem({
  title,
  detail,
  amount,
  tone = 'normal',
  testID,
}: {
  title: string;
  detail?: string;
  amount: string;
  tone?: 'normal' | 'alert' | 'good';
  testID?: string;
}) {
  return (
    <View style={styles.row} testID={testID} accessible accessibilityLabel={`${title}, ${amount}`}>
      <View style={styles.left}>
        <Text style={styles.title}>{title}</Text>
        {detail !== undefined ? <Text style={styles.detail}>{detail}</Text> : null}
      </View>
      <Text style={[styles.amount, tone === 'alert' && styles.alert, tone === 'good' && styles.good]}>
        {amount}
      </Text>
    </View>
  );
}

export function ObligationRow({ item }: { item: Obligation }) {
  const open = item.status === 'open';
  return (
    <LineItem
      title={t(`obligation.${item.kind}`)}
      detail={`${t('obligation.due', { date: formatDate(item.due_date) })} · ${t(`obligation.status.${item.status}`)}`}
      amount={open ? formatKes(item.balance_minor) : formatKes(item.amount_minor)}
      tone={open && item.kind === 'fine' ? 'alert' : 'normal'}
      testID={`obligation-${item.obligation_id}`}
    />
  );
}

export function PaymentRow({ item, name }: { item: Payment; name?: string }) {
  return (
    <LineItem
      title={name ?? t(`payment.source.${item.source}`)}
      detail={`${formatDayMonth(item.received_at)} · ${item.mpesa_receipt} · ${t(`payment.status.${item.status}`)}`}
      amount={formatKes(item.amount_minor)}
      tone="good"
      testID={`payment-${item.payment_id}`}
    />
  );
}

const STATEMENT_ACCOUNTS = new Set([
  'member_savings',
  'fines_receivable',
  'loans_receivable',
  'interest_receivable',
]);

export function StatementRow({ item }: { item: StatementEntry }) {
  const account = STATEMENT_ACCOUNTS.has(item.account) ? item.account : 'other';
  return (
    <LineItem
      title={t(`statement.account.${account}` as 'statement.account.other')}
      detail={`${formatDayMonth(item.created_at)}${item.memo === '' ? '' : ` · ${item.memo}`}`}
      amount={formatKes(item.amount_minor)}
    />
  );
}

export function CashbookRow({ item }: { item: CashbookTransaction }) {
  return (
    <LineItem
      title={item.memo === '' ? item.kind : item.memo}
      detail={formatDayMonth(item.created_at)}
      amount={formatKes(item.cash_minor)}
      tone={item.cash_minor < 0 ? 'alert' : 'good'}
    />
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: c.line,
    gap: 12,
    minHeight: 48,
  },
  left: { flex: 1, gap: 2 },
  title: { fontSize: 15, fontWeight: '600', color: c.ink },
  detail: { fontSize: 13, color: c.muted },
  amount: { fontSize: 15, fontWeight: '600', color: c.ink },
  alert: { color: c.red },
  good: { color: c.green },
});
