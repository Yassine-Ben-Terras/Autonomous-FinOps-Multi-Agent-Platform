/**
 * CloudSense FinOps — Grafana Plugin Entrypoint (Phase 5.2)
 *
 * Registers the datasource with Grafana's plugin system.
 * Grafana loads this file as the plugin's main module.
 *
 * Plugin ID: cloudsense-finops-datasource
 * Published: https://grafana.com/plugins/cloudsense-finops-datasource
 */

import { DataSourcePlugin } from '@grafana/data';
import { DataSource } from './datasource';
import { QueryEditor } from './QueryEditor';
import { ConfigEditor } from './ConfigEditor';
import { CloudSenseDataSourceOptions, CloudSenseQuery } from './types';

export const plugin = new DataSourcePlugin<DataSource, CloudSenseQuery, CloudSenseDataSourceOptions>(
  DataSource
)
  .setConfigEditor(ConfigEditor)
  .setQueryEditor(QueryEditor);
