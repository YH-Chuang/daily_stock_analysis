import type React from 'react';
import { Activity } from 'lucide-react';
import { Badge, Card, EmptyState, Loading } from '../common';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiLanguage, UiTextKey, UiTextParams } from '../../i18n/uiText';
import type { AlertTriggerItem } from '../../types/alerts';
import { formatDateTime } from '../../utils/format';
import { getMarketPhaseSummaryLabel } from '../../utils/marketPhase';

type Translate = (key: UiTextKey, params?: UiTextParams) => string;

const statusTextKey: Record<string, UiTextKey> = {
  triggered: 'alerts.triggerHistory.statusTriggered',
  skipped: 'alerts.triggerHistory.statusSkipped',
  degraded: 'alerts.triggerHistory.statusDegraded',
  failed: 'alerts.triggerHistory.statusFailed',
};

function statusLabel(status: string, t: Translate): string {
  const key = statusTextKey[status];
  return key ? t(key) : status;
}

function statusVariant(status: string): 'success' | 'warning' | 'danger' | 'default' {
  if (status === 'triggered') return 'success';
  if (status === 'skipped' || status === 'degraded') return 'warning';
  if (status === 'failed') return 'danger';
  return 'default';
}

function formatNullable(value?: string | number | null): string {
  if (value === null || value === undefined || value === '') return '--';
  return String(value);
}

function renderPhaseQuality(trigger: AlertTriggerItem, t: Translate, language: UiLanguage): React.ReactNode {
  const phaseLabel = getMarketPhaseSummaryLabel(trigger.marketPhaseSummary, language);
  // getMarketPhaseSummaryLabel 会带上本地化前缀（如 `市场阶段: `），这里按分隔符剥离，避免写死各语言前缀
  const separatorIndex = phaseLabel ? phaseLabel.indexOf(': ') : -1;
  const phase = phaseLabel && separatorIndex >= 0 ? phaseLabel.slice(separatorIndex + 2) : phaseLabel;
  const quality = trigger.analysisContextPackOverview?.dataQuality?.level;
  const limitations = trigger.analysisContextPackOverview?.dataQuality?.limitations?.slice(0, 2) ?? [];
  if (!phase && !quality && limitations.length === 0) {
    return <span className="text-xs text-muted-text">--</span>;
  }
  return (
    <div className="space-y-1">
      {phase ? <Badge variant="default">{phase}</Badge> : null}
      {quality ? (
        <div className="text-xs text-secondary-text">
          {t('alerts.triggerHistory.quality', { value: quality })}
        </div>
      ) : null}
      {limitations.length ? (
        <div className="max-w-[180px] text-xs text-muted-text">
          {limitations.join(language === 'en' ? '; ' : '；')}
        </div>
      ) : null}
    </div>
  );
}

interface AlertTriggerHistoryProps {
  triggers: AlertTriggerItem[];
  isLoading?: boolean;
}

export const AlertTriggerHistory: React.FC<AlertTriggerHistoryProps> = ({ triggers, isLoading = false }) => {
  const { t, language } = useUiLanguage();

  return (
    <Card
      title={t('alerts.triggerHistory.title')}
      subtitle={t('alerts.triggerHistory.subtitle')}
      variant="bordered"
      padding="md"
    >
      {isLoading ? <Loading label={t('alerts.triggerHistory.loading')} /> : null}
      {!isLoading && triggers.length === 0 ? (
        <EmptyState
          icon={<Activity className="h-6 w-6" />}
          title={t('alerts.triggerHistory.emptyTitle')}
          description={t('alerts.triggerHistory.emptyDescription')}
        />
      ) : null}
      {!isLoading && triggers.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-left text-sm">
            <thead className="border-b border-border/60 text-xs uppercase text-muted-text">
              <tr>
                <th className="px-3 py-2 font-medium">{t('alerts.triggerHistory.columnStatus')}</th>
                <th className="px-3 py-2 font-medium">{t('alerts.triggerHistory.columnPhaseQuality')}</th>
                <th className="px-3 py-2 font-medium">{t('alerts.triggerHistory.columnTarget')}</th>
                <th className="px-3 py-2 font-medium">{t('alerts.triggerHistory.columnObservedValue')}</th>
                <th className="px-3 py-2 font-medium">{t('alerts.triggerHistory.columnThreshold')}</th>
                <th className="px-3 py-2 font-medium">{t('alerts.triggerHistory.columnDataSource')}</th>
                <th className="px-3 py-2 font-medium">{t('alerts.triggerHistory.columnDataTime')}</th>
                <th className="px-3 py-2 font-medium">{t('alerts.triggerHistory.columnReason')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {triggers.map((trigger) => (
                <tr key={trigger.id} className="align-top">
                  <td className="px-3 py-3">
                    <Badge variant={statusVariant(trigger.status)}>
                      {statusLabel(trigger.status, t)}
                    </Badge>
                  </td>
                  <td className="px-3 py-3">{renderPhaseQuality(trigger, t, language)}</td>
                  <td className="px-3 py-3 font-mono text-secondary-text">{trigger.target}</td>
                  <td className="px-3 py-3 text-secondary-text">{formatNullable(trigger.observedValue)}</td>
                  <td className="px-3 py-3 text-secondary-text">{formatNullable(trigger.threshold)}</td>
                  <td className="px-3 py-3 text-secondary-text">{formatNullable(trigger.dataSource)}</td>
                  <td className="px-3 py-3 text-xs text-secondary-text">
                    {formatDateTime(trigger.dataTimestamp ?? trigger.triggeredAt)}
                  </td>
                  <td className="px-3 py-3 text-secondary-text">
                    {trigger.reason || trigger.diagnostics || '--'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Card>
  );
};
