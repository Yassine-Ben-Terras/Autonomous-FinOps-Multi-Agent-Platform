/**
 * CloudSense FinOps — Grafana Datasource (Phase 5.2)
 *
 * Implements the Grafana DataSourceApi so the plugin renders in
 * TimeSeries, Table, Stat, and Gauge panels.
 *
 * Query flow:
 *   Grafana panel editor → QueryEditor.tsx builds query object
 *   → datasource.query() sends POST to CloudSense API
 *   → response transformed to Grafana DataFrame format
 *   → Grafana renders the panel
 *
 * Datasource options (configured in Grafana plugin settings):
 *   apiUrl      — CloudSense API base URL
 *   tenantSlug  — CloudSense tenant identifier
 *   apiToken    — JWT access token (stored as secureJsonData)
 */

import {
  DataQueryRequest,
  DataQueryResponse,
  DataSourceApi,
  DataSourceInstanceSettings,
  FieldType,
  MutableDataFrame,
  TimeRange,
} from '@grafana/data';
import { getBackendSrv } from '@grafana/runtime';

import {
  CloudSenseDataSourceOptions,
  CloudSenseQuery,
  DEFAULT_QUERY,
  MetricType,
  QueryType,
} from './types';

export class DataSource extends DataSourceApi<CloudSenseQuery, CloudSenseDataSourceOptions> {
  private readonly apiUrl: string;
  private readonly tenantSlug: string;

  constructor(instanceSettings: DataSourceInstanceSettings<CloudSenseDataSourceOptions>) {
    super(instanceSettings);
    this.apiUrl = instanceSettings.jsonData.apiUrl?.replace(/\/$/, '') ?? '';
    this.tenantSlug = instanceSettings.jsonData.tenantSlug ?? '';
  }

  // ── Health check ──────────────────────────────────────────────

  async testDatasource(): Promise<{ status: string; message: string }> {
    try {
      const result = await this.request<{ status: string; message: string }>(
        'GET',
        '/api/v1/exports/grafana/health'
      );
      return { status: result.status === 'ok' ? 'success' : 'error', message: result.message };
    } catch (err) {
      return { status: 'error', message: `Cannot connect to CloudSense: ${String(err)}` };
    }
  }

  // ── Metric search (QueryEditor autocomplete) ──────────────────

  async metricFindQuery(query: string): Promise<Array<{ text: string; value: string }>> {
    const result = await this.request<string[]>('POST', '/api/v1/exports/grafana/search', {
      query,
    });
    return result.map((m) => ({ text: m, value: m }));
  }

  // ── Main query handler ────────────────────────────────────────

  async query(options: DataQueryRequest<CloudSenseQuery>): Promise<DataQueryResponse> {
    const { range, targets } = options;

    // Filter hidden targets and targets without metric configured
    const activeTargets = targets.filter((t) => !t.hide && t.metric);

    if (activeTargets.length === 0) {
      return { data: [] };
    }

    const from = range.from.toISOString();
    const to = range.to.toISOString();

    const frames = await Promise.all(
      activeTargets.map((target) =>
        this.executeTarget(target, from, to, options.maxDataPoints ?? 500)
      )
    );

    return { data: frames.flat() };
  }

  // ── Per-target execution ──────────────────────────────────────

  private async executeTarget(
    target: CloudSenseQuery,
    from: string,
    to: string,
    maxPoints: number
  ): Promise<MutableDataFrame[]> {
    const payload = {
      targets: [
        {
          refId: target.refId,
          queryType: target.queryType ?? QueryType.Timeseries,
          metric: target.metric,
          provider: target.provider,
          service: target.service,
          region: target.region,
          groupBy: target.groupBy ?? [],
          granularity: target.granularity ?? 'daily',
        },
      ],
      range: { from, to },
      maxDataPoints: maxPoints,
      tenantSlug: this.tenantSlug,
    };

    const response = await this.request<GrafanaQueryResponse>(
      'POST',
      '/api/v1/exports/grafana/query',
      payload
    );

    return this.transformResponse(response, target);
  }

  // ── Response → Grafana DataFrame transform ───────────────────

  private transformResponse(
    response: GrafanaQueryResponse,
    target: CloudSenseQuery
  ): MutableDataFrame[] {
    const frames: MutableDataFrame[] = [];

    for (const result of response.results ?? []) {
      if (target.queryType === QueryType.Table) {
        frames.push(this.buildTableFrame(result, target));
      } else {
        frames.push(...this.buildTimeseriesFrames(result, target));
      }
    }

    return frames;
  }

  private buildTimeseriesFrames(result: QueryResult, target: CloudSenseQuery): MutableDataFrame[] {
    const frames: MutableDataFrame[] = [];

    for (const series of result.series ?? []) {
      const frame = new MutableDataFrame({
        refId: target.refId,
        name: series.name ?? target.metric,
        fields: [
          { name: 'Time',  type: FieldType.time,   values: [] as number[] },
          { name: 'Value', type: FieldType.number,  values: [] as number[], config: { unit: 'currencyUSD' } },
        ],
      });

      for (const point of series.points ?? []) {
        frame.add({ Time: point[1] * 1000, Value: point[0] });
      }

      frames.push(frame);
    }

    return frames;
  }

  private buildTableFrame(result: QueryResult, target: CloudSenseQuery): MutableDataFrame {
    const columns = result.columns ?? [];
    const frame = new MutableDataFrame({
      refId: target.refId,
      name: target.metric,
      fields: columns.map((col) => ({
        name: col.text,
        type: col.type === 'number' ? FieldType.number : FieldType.string,
        values: [] as (string | number)[],
        config: col.type === 'number' ? { unit: 'currencyUSD' } : {},
      })),
    });

    for (const row of result.rows ?? []) {
      const obj: Record<string, string | number> = {};
      columns.forEach((col, i) => { obj[col.text] = row[i]; });
      frame.add(obj);
    }

    return frame;
  }

  // ── Annotations ───────────────────────────────────────────────

  async annotationQuery(options: {
    range: TimeRange;
    annotation: { query?: string };
  }): Promise<GrafanaAnnotation[]> {
    const result = await this.request<{ annotations: GrafanaAnnotation[] }>(
      'GET',
      '/api/v1/exports/grafana/annotations',
      undefined,
      {
        from: options.range.from.valueOf(),
        to: options.range.to.valueOf(),
        query: options.annotation.query ?? '',
      }
    );
    return result.annotations ?? [];
  }

  // ── HTTP helper ───────────────────────────────────────────────

  private async request<T>(
    method: 'GET' | 'POST',
    path: string,
    body?: unknown,
    params?: Record<string, string | number>
  ): Promise<T> {
    let url = `${this.apiUrl}${path}`;
    if (params) {
      const qs = new URLSearchParams(
        Object.entries(params).map(([k, v]) => [k, String(v)])
      );
      url = `${url}?${qs}`;
    }

    const options: RequestInit = {
      method,
      headers: {
        'Content-Type': 'application/json',
        'X-CloudSense-Tenant': this.tenantSlug,
      },
    };
    if (body) {
      options.body = JSON.stringify(body);
    }

    const response = await getBackendSrv().fetch<T>({ url, method, data: body, params }).toPromise();
    if (!response) throw new Error('No response from CloudSense API');
    return response.data;
  }
}

// ── Internal types ─────────────────────────────────────────────

interface GrafanaQueryResponse {
  results?: QueryResult[];
}

interface QueryResult {
  refId?: string;
  series?: Array<{ name: string; points: Array<[number, number]> }>;
  columns?: Array<{ text: string; type: string }>;
  rows?: Array<Array<string | number>>;
}

interface GrafanaAnnotation {
  time: number;
  timeEnd?: number;
  title: string;
  text: string;
  tags: string[];
}
