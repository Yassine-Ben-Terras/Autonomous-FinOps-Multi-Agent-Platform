/**
 * CloudSense FinOps Grafana Plugin — Shared Types (Phase 5.2)
 */

import { DataQuery, DataSourceJsonData } from '@grafana/data';

// ── Query types ───────────────────────────────────────────────

export enum QueryType {
  Timeseries = 'timeseries',
  Table      = 'table',
  Annotation = 'annotation',
}

export enum MetricType {
  CostTotal         = 'cost.total',
  CostByService     = 'cost.by_service',
  CostByProvider    = 'cost.by_provider',
  CostByRegion      = 'cost.by_region',
  CostByAccount     = 'cost.by_account',
  SavingsPotential  = 'savings.potential',
  AnomalyCount      = 'anomaly.count',
  AnomalyCostDelta  = 'anomaly.cost_delta',
  Forecast30d       = 'forecast.30d',
  Forecast60d       = 'forecast.60d',
  Forecast90d       = 'forecast.90d',
  K8sByNamespace    = 'k8s.cost.by_namespace',
  K8sByWorkload     = 'k8s.cost.by_workload',
  TagCompliance     = 'tags.compliance_score',
  CommitmentCoverage = 'commitment.coverage_pct',
}

export enum Granularity {
  Hourly  = 'hourly',
  Daily   = 'daily',
  Weekly  = 'weekly',
  Monthly = 'monthly',
}

export interface CloudSenseQuery extends DataQuery {
  queryType?: QueryType;
  metric?: MetricType | string;
  provider?: 'aws' | 'azure' | 'gcp' | '';
  service?: string;
  region?: string;
  account?: string;
  groupBy?: string[];
  granularity?: Granularity;
  limit?: number;
}

export const DEFAULT_QUERY: Partial<CloudSenseQuery> = {
  queryType:   QueryType.Timeseries,
  metric:      MetricType.CostTotal,
  provider:    '',
  granularity: Granularity.Daily,
  groupBy:     [],
};

// ── Datasource config ─────────────────────────────────────────

export interface CloudSenseDataSourceOptions extends DataSourceJsonData {
  apiUrl?:      string;
  tenantSlug?:  string;
}

export interface CloudSenseSecureJsonData {
  apiToken?: string;
}
