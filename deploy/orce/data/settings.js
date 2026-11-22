// Node-RED settings of the FAP-light server (ORCE, light profile).
// functionExternalModules: true allows require('pg'|'ssh2-sftp-client'|...)
// from function nodes (modules installed in /data/node_modules).
module.exports = {
  flowFile: 'flows.json',
  userDir: '/data',
  httpAdminRoot: '/',
  httpNodeRoot: '/',
  uiPort: process.env.PORT || 1880,
  functionExternalModules: true,
  functionTimeout: 30000,
  httpNodeCors: { origin: '*' },
  credentialSecret: process.env.NODE_RED_CREDENTIAL_SECRET || 'fap-light-credential-secret',
  logging: {
    console: { level: 'info', metrics: false, audit: false }
  }
};
