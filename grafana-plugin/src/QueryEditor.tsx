/**
 * CloudSense FinOps — Grafana Query Editor (Phase 5.2)
 *
 * Renders the panel editor UI that engineers use to configure
 * CloudSense queries in Grafana. Shows dropdowns for:
 *   - Query type (timeseries / table)
 *   - Metric selector
 *   - Provider / service / region filters
 *   - Granularity and group-by
 */

import React, { ChangeEvent, PureComponent } from 'react';
import { InlineField, InlineFieldRow, Select, MultiSelect, Input } from '@grafana/ui';
import { QueryEditorProps, SelectableValue } from '@grafana/data';
import { DataSource } from './datasource';
import {
  CloudSenseDataSourceOptions,
  CloudSenseQuery,
  DEFAULT_QUERY,
  Granularity,
  MetricType,
  QueryType,
} from './types';

type Props = QueryEditorProps<DataSource, CloudSenseQuery, CloudSenseDataSourceOptions>;

const QUERY_TYPE_OPTIONS: Array<SelectableValue<QueryType>> = [
  { label: 'Time series', value: QueryType.Timeseries, description: 'Cost over time — use with Time series panel' },
  { label: 'Table',       value: QueryType.Table,      description: 'Cost breakdown table — use with Table panel' },
];

const METRIC_OPTIONS: Array<SelectableValue<MetricType>> = [
  { label: 'Total cost',            value: MetricType.CostTotal,          description: 'Aggregate spend across all clouds' },
  { label: 'Cost by service',       value: MetricType.CostByService,      description: 'Breakdown per cloud service' },
  { label: 'Cost by provider',      value: MetricType.CostByProvider,     description: 'AWS vs Azure vs GCP' },
  { label: 'Cost by region',        value: MetricType.CostByRegion,       description: 'Geographic cost distribution' },
  { label: 'Cost by account',       value: MetricType.CostByAccount,      description: 'Per account/subscription/project' },
  { label: 'Savings potential',     value: MetricType.SavingsPotential,   description: 'Agent-detected savings opportunities' },
  { label: 'Anomaly count',         value: MetricType.AnomalyCount,       description: 'Number of billing anomalies detected' },
  { label: 'Anomaly cost delta',    value: MetricType.AnomalyCostDelta,   description: 'Dollar value of detected anomalies' },
  { label: 'Forecast 30d',          value: MetricType.Forecast30d,        description: '30-day spend forecast' },
  { label: 'Forecast 60d',          value: MetricType.Forecast60d,        description: '60-day spend forecast' },
  { label: 'Forecast 90d',          value: MetricType.Forecast90d,        description: '90-day spend forecast' },
  { label: 'K8s — by namespace',    value: MetricType.K8sByNamespace,     description: 'Kubernetes cost per namespace' },
  { label: 'K8s — by workload',     value: MetricType.K8sByWorkload,      description: 'Kubernetes cost per deployment/daemonset' },
  { label: 'Tag compliance score',  value: MetricType.TagCompliance,      description: '% of resources with required tags' },
  { label: 'Commitment coverage %', value: MetricType.CommitmentCoverage, description: 'RI / Savings Plan / CUD coverage' },
];

const PROVIDER_OPTIONS: Array<SelectableValue<string>> = [
  { label: 'All providers', value: '' },
  { label: 'AWS',           value: 'aws' },
  { label: 'Azure',         value: 'azure' },
  { label: 'GCP',           value: 'gcp' },
];

const GRANULARITY_OPTIONS: Array<SelectableValue<Granularity>> = [
  { label: 'Hourly',  value: Granularity.Hourly },
  { label: 'Daily',   value: Granularity.Daily },
  { label: 'Weekly',  value: Granularity.Weekly },
  { label: 'Monthly', value: Granularity.Monthly },
];

const GROUP_BY_OPTIONS: Array<SelectableValue<string>> = [
  { label: 'Provider',  value: 'provider' },
  { label: 'Service',   value: 'service_name' },
  { label: 'Region',    value: 'region_id' },
  { label: 'Account',   value: 'billing_account_id' },
  { label: 'Team tag',  value: 'tag_team' },
  { label: 'Env tag',   value: 'tag_env' },
];

export class QueryEditor extends PureComponent<Props> {
  private get query(): CloudSenseQuery {
    return { ...DEFAULT_QUERY, ...this.props.query } as CloudSenseQuery;
  }

  private onChange<K extends keyof CloudSenseQuery>(key: K, value: CloudSenseQuery[K]) {
    const { onChange, onRunQuery } = this.props;
    onChange({ ...this.query, [key]: value });
    onRunQuery();
  }

  render() {
    const q = this.query;

    return (
      <div>
        {/* Row 1: Query type + Metric */}
        <InlineFieldRow>
          <InlineField label="Query type" labelWidth={14} tooltip="How to render the results">
            <Select
              width={18}
              options={QUERY_TYPE_OPTIONS}
              value={q.queryType}
              onChange={(v) => this.onChange('queryType', v.value!)}
            />
          </InlineField>
          <InlineField label="Metric" labelWidth={10} tooltip="What data to fetch from CloudSense">
            <Select
              width={36}
              options={METRIC_OPTIONS}
              value={q.metric}
              onChange={(v) => this.onChange('metric', v.value!)}
            />
          </InlineField>
        </InlineFieldRow>

        {/* Row 2: Provider + Granularity */}
        <InlineFieldRow>
          <InlineField label="Provider" labelWidth={14} tooltip="Filter by cloud provider">
            <Select
              width={18}
              options={PROVIDER_OPTIONS}
              value={q.provider ?? ''}
              onChange={(v) => this.onChange('provider', v.value as any)}
            />
          </InlineField>
          <InlineField label="Granularity" labelWidth={14} tooltip="Time bucket size for aggregation">
            <Select
              width={16}
              options={GRANULARITY_OPTIONS}
              value={q.granularity}
              onChange={(v) => this.onChange('granularity', v.value!)}
            />
          </InlineField>
        </InlineFieldRow>

        {/* Row 3: Service + Region filters */}
        <InlineFieldRow>
          <InlineField label="Service" labelWidth={14} tooltip="Filter by cloud service name (e.g. EC2, Compute Engine)">
            <Input
              width={24}
              placeholder="All services"
              value={q.service ?? ''}
              onChange={(e: ChangeEvent<HTMLInputElement>) => this.onChange('service', e.currentTarget.value)}
              onBlur={() => this.props.onRunQuery()}
            />
          </InlineField>
          <InlineField label="Region" labelWidth={10} tooltip="Filter by region (e.g. us-east-1)">
            <Input
              width={20}
              placeholder="All regions"
              value={q.region ?? ''}
              onChange={(e: ChangeEvent<HTMLInputElement>) => this.onChange('region', e.currentTarget.value)}
              onBlur={() => this.props.onRunQuery()}
            />
          </InlineField>
        </InlineFieldRow>

        {/* Row 4: Group by (for table queries) */}
        {q.queryType === QueryType.Table && (
          <InlineFieldRow>
            <InlineField label="Group by" labelWidth={14} tooltip="Dimensions to aggregate by in table view">
              <MultiSelect
                width={48}
                options={GROUP_BY_OPTIONS}
                value={q.groupBy ?? []}
                onChange={(v) => this.onChange('groupBy', v.map((x) => x.value!))}
              />
            </InlineField>
          </InlineFieldRow>
        )}
      </div>
    );
  }
}
