/**
 * CloudSense FinOps — Grafana Config Editor (Phase 5.2)
 *
 * Renders the datasource configuration form in Grafana → Connections
 * → Data Sources → CloudSense FinOps → Settings.
 *
 * Fields:
 *   apiUrl      — CloudSense API base URL (stored as jsonData)
 *   tenantSlug  — Tenant slug (stored as jsonData)
 *   apiToken    — JWT access token (stored as secureJsonData — encrypted)
 */

import React, { ChangeEvent, PureComponent } from 'react';
import { InlineField, InlineFieldRow, Input, SecretInput } from '@grafana/ui';
import { DataSourcePluginOptionsEditorProps } from '@grafana/data';
import { CloudSenseDataSourceOptions, CloudSenseSecureJsonData } from './types';

type Props = DataSourcePluginOptionsEditorProps<CloudSenseDataSourceOptions, CloudSenseSecureJsonData>;

export class ConfigEditor extends PureComponent<Props> {
  private onApiUrlChange = (e: ChangeEvent<HTMLInputElement>) => {
    const { onOptionsChange, options } = this.props;
    onOptionsChange({
      ...options,
      jsonData: { ...options.jsonData, apiUrl: e.currentTarget.value },
    });
  };

  private onTenantSlugChange = (e: ChangeEvent<HTMLInputElement>) => {
    const { onOptionsChange, options } = this.props;
    onOptionsChange({
      ...options,
      jsonData: { ...options.jsonData, tenantSlug: e.currentTarget.value },
    });
  };

  private onApiTokenChange = (e: ChangeEvent<HTMLInputElement>) => {
    const { onOptionsChange, options } = this.props;
    onOptionsChange({
      ...options,
      secureJsonData: { apiToken: e.currentTarget.value },
    });
  };

  private onResetApiToken = () => {
    const { onOptionsChange, options } = this.props;
    onOptionsChange({
      ...options,
      secureJsonFields: { ...options.secureJsonFields, apiToken: false },
      secureJsonData: { apiToken: '' },
    });
  };

  render() {
    const { options } = this.props;
    const { jsonData, secureJsonFields } = options;
    const secureJsonData = (options.secureJsonData ?? {}) as CloudSenseSecureJsonData;

    return (
      <div>
        <InlineFieldRow>
          <InlineField
            label="CloudSense API URL"
            labelWidth={22}
            tooltip="Base URL of your CloudSense instance (e.g. https://cloudsense.yourdomain.com)"
            required
          >
            <Input
              width={40}
              name="apiUrl"
              value={jsonData.apiUrl ?? ''}
              placeholder="https://cloudsense.yourdomain.com"
              onChange={this.onApiUrlChange}
            />
          </InlineField>
        </InlineFieldRow>

        <InlineFieldRow>
          <InlineField
            label="Tenant Slug"
            labelWidth={22}
            tooltip="Your CloudSense tenant identifier (found in your account settings)"
            required
          >
            <Input
              width={24}
              name="tenantSlug"
              value={jsonData.tenantSlug ?? ''}
              placeholder="acme-corp"
              onChange={this.onTenantSlugChange}
            />
          </InlineField>
        </InlineFieldRow>

        <InlineFieldRow>
          <InlineField
            label="API Token"
            labelWidth={22}
            tooltip="CloudSense JWT access token — generate via POST /auth/login. Stored encrypted."
            required
          >
            <SecretInput
              width={40}
              name="apiToken"
              value={secureJsonData.apiToken ?? ''}
              isConfigured={Boolean(secureJsonFields?.apiToken)}
              placeholder="eyJhbGciOiJIUzI1NiIs..."
              onChange={this.onApiTokenChange}
              onReset={this.onResetApiToken}
            />
          </InlineField>
        </InlineFieldRow>
      </div>
    );
  }
}
