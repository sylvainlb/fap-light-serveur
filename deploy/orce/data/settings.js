// Settings Node-RED du serveur FAP-light (ORCE, profil light).
// functionExternalModules: true autorise require('pg'|'ssh2-sftp-client'|...)
// depuis les function nodes (modules installés dans /data/node_modules).
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
