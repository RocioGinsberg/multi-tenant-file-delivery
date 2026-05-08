/* ══════════════════════════════════════════════════════════
   Smart Ops — Chinese Status Vocabulary (status-vocab.js)
   ══════════════════════════════════════════════════════════ */
(function (global) {
  'use strict';

  var EXECUTION_STATUS = {
    queued:            '排队中',
    running:           '运行中',
    completed:         '已完成',
    failed:            '失败',
    execution_failed:  '执行失败',
    pending:           '待处理',
    cancelled:         '已取消',
    retrying:          '重试中',
  };

  var EXECUTION_DOMAIN = {
    approval_execute: '审批执行',
    knowledge_index:  '知识索引',
    upload:           '上传',
    cosdrive_upload:  '企业网盘上传',
  };

  var APPROVAL_STATUS = {
    pending:    '待审批',
    approved:   '已通过',
    rejected:   '已拒绝',
    returned:   '已退回',
  };

  var DICT_STATUS = {
    active:     '启用',
    deprecated: '已弃用',
    draft:      '草稿',
  };

  var SOURCE_TYPE = {
    file:   '文件',
    api:    '接口',
    enrich: '增强',
  };

  var RETENTION_CLASS = {
    ephemeral: '临时对话',
    standard:  '标准保留',
    permanent: '长期保留',
  };

  function label(vocab, code) {
    if (!code) return '—';
    return (vocab && vocab[code]) || code;
  }

  global.PortalVocab = {
    EXECUTION_STATUS:  EXECUTION_STATUS,
    EXECUTION_DOMAIN:  EXECUTION_DOMAIN,
    APPROVAL_STATUS:   APPROVAL_STATUS,
    DICT_STATUS:       DICT_STATUS,
    SOURCE_TYPE:       SOURCE_TYPE,
    RETENTION_CLASS:   RETENTION_CLASS,
    label:             label,
  };
})(window);
