/**
 * marked-lite.js — Lightweight GFM Markdown parser
 * API-compatible with: marked.setOptions(), marked.parse()
 * Covers: headings, bold, italic, strikethrough, code blocks,
 *   inline code, tables, lists, links, images, blockquotes,
 *   horizontal rules, line breaks, paragraphs.
 */
(function (global) {
  'use strict';

  var opts = { breaks: false, gfm: true };

  function setOptions(o) {
    if (o) { for (var k in o) opts[k] = o[k]; }
  }

  /* ── helpers ── */
  function esc(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function inlineRules(text) {
    // images ![alt](src)
    text = text.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img alt="$1" src="$2" style="max-width:100%"/>');
    // links [text](url)
    text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    // inline code (must be before bold/italic to avoid conflicts)
    text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
    // bold + italic ***text*** or ___text___
    text = text.replace(/\*{3}(.+?)\*{3}/g, '<strong><em>$1</em></strong>');
    text = text.replace(/_{3}(.+?)_{3}/g, '<strong><em>$1</em></strong>');
    // bold **text** or __text__
    text = text.replace(/\*{2}(.+?)\*{2}/g, '<strong>$1</strong>');
    text = text.replace(/_{2}(.+?)_{2}/g, '<strong>$1</strong>');
    // italic *text* or _text_
    text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
    text = text.replace(/(?<![a-zA-Z0-9])_(.+?)_(?![a-zA-Z0-9])/g, '<em>$1</em>');
    // strikethrough ~~text~~
    text = text.replace(/~~(.+?)~~/g, '<del>$1</del>');
    return text;
  }

  /* ── block-level parser ── */
  function parse(md) {
    if (typeof md !== 'string') md = String(md || '');
    var lines = md.replace(/\r\n/g, '\n').split('\n');
    var html = '';
    var i = 0;
    var len = lines.length;

    while (i < len) {
      var line = lines[i];

      // ── fenced code block ───────────────────────
      var fenceMatch = line.match(/^(`{3,}|~{3,})\s*([\w-]*)/);
      if (fenceMatch) {
        var fence = fenceMatch[1].charAt(0);
        var fenceLen = fenceMatch[1].length;
        var lang = fenceMatch[2] || '';
        var code = [];
        i++;
        while (i < len) {
          // close fence: same char, at least as many
          if (new RegExp('^' + fence + '{' + fenceLen + ',}\\s*$').test(lines[i])) { i++; break; }
          code.push(esc(lines[i]));
          i++;
        }
        html += '<pre' + (lang ? ' class="language-' + esc(lang) + '"' : '') + '><code>' + code.join('\n') + '</code></pre>\n';
        continue;
      }

      // ── heading ─────────────────────────────────
      var headMatch = line.match(/^(#{1,6})\s+(.+?)(\s+#+)?$/);
      if (headMatch) {
        var level = headMatch[1].length;
        html += '<h' + level + '>' + inlineRules(esc(headMatch[2])) + '</h' + level + '>\n';
        i++; continue;
      }

      // ── horizontal rule ─────────────────────────
      if (/^(\*{3,}|-{3,}|_{3,})\s*$/.test(line)) {
        html += '<hr/>\n';
        i++; continue;
      }

      // ── blockquote ──────────────────────────────
      if (/^>\s?/.test(line)) {
        var bqLines = [];
        while (i < len && /^>\s?/.test(lines[i])) {
          bqLines.push(lines[i].replace(/^>\s?/, ''));
          i++;
        }
        html += '<blockquote>' + parse(bqLines.join('\n')) + '</blockquote>\n';
        continue;
      }

      // ── table (GFM) ────────────────────────────
      if (opts.gfm && line.indexOf('|') !== -1 && i + 1 < len && /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(lines[i + 1])) {
        var headerCells = parseTableRow(line);
        var alignRow = parseTableRow(lines[i + 1]);
        var aligns = alignRow.map(function (c) {
          c = c.trim();
          if (c.charAt(0) === ':' && c.charAt(c.length - 1) === ':') return 'center';
          if (c.charAt(c.length - 1) === ':') return 'right';
          return 'left';
        });
        var tbl = '<table><thead><tr>';
        headerCells.forEach(function (c, ci) {
          tbl += '<th style="text-align:' + (aligns[ci] || 'left') + '">' + inlineRules(esc(c.trim())) + '</th>';
        });
        tbl += '</tr></thead><tbody>';
        i += 2;
        while (i < len && lines[i].indexOf('|') !== -1 && lines[i].trim()) {
          var cells = parseTableRow(lines[i]);
          tbl += '<tr>';
          for (var ci = 0; ci < headerCells.length; ci++) {
            tbl += '<td style="text-align:' + (aligns[ci] || 'left') + '">' + inlineRules(esc((cells[ci] || '').trim())) + '</td>';
          }
          tbl += '</tr>';
          i++;
        }
        tbl += '</tbody></table>';
        html += tbl + '\n';
        continue;
      }

      // ── unordered list ──────────────────────────
      if (/^[\s]*[-*+]\s+/.test(line)) {
        html += parseList(lines, i, 'ul');
        // advance i past the list
        while (i < len && (/^[\s]*[-*+]\s+/.test(lines[i]) || /^\s+\S/.test(lines[i]))) i++;
        // skip trailing blank
        if (i < len && !lines[i].trim()) i++;
        continue;
      }

      // ── ordered list ────────────────────────────
      if (/^[\s]*\d+[.)]\s+/.test(line)) {
        html += parseList(lines, i, 'ol');
        while (i < len && (/^[\s]*\d+[.)]\s+/.test(lines[i]) || /^\s+\S/.test(lines[i]))) i++;
        if (i < len && !lines[i].trim()) i++;
        continue;
      }

      // ── blank line ──────────────────────────────
      if (!line.trim()) { i++; continue; }

      // ── paragraph (default) ─────────────────────
      var paraLines = [];
      while (i < len && lines[i].trim() && !/^#{1,6}\s/.test(lines[i]) && !/^(`{3,}|~{3,})/.test(lines[i]) && !/^>\s?/.test(lines[i]) && !/^(\*{3,}|-{3,}|_{3,})\s*$/.test(lines[i]) && !/^[\s]*[-*+]\s+/.test(lines[i]) && !/^[\s]*\d+[.)]\s+/.test(lines[i])) {
        paraLines.push(lines[i]);
        i++;
      }
      if (paraLines.length) {
        var pText = paraLines.map(function (l) { return esc(l); });
        var separator = opts.breaks ? '<br/>' : ' ';
        html += '<p>' + inlineRules(pText.join(separator)) + '</p>\n';
      }
    }

    return html;
  }

  /* ── table row parser ── */
  function parseTableRow(line) {
    line = line.trim();
    if (line.charAt(0) === '|') line = line.substring(1);
    if (line.charAt(line.length - 1) === '|') line = line.substring(0, line.length - 1);
    return line.split('|');
  }

  /* ── list parser ── */
  function parseList(lines, start, tag) {
    var items = [];
    var i = start;
    var len = lines.length;
    var itemRe = tag === 'ul' ? /^[\s]*[-*+]\s+(.*)/ : /^[\s]*\d+[.)]\s+(.*)/;

    while (i < len) {
      var m = lines[i].match(itemRe);
      if (m) {
        items.push(m[1]);
        i++;
        // continuation lines (indented)
        while (i < len && /^\s+\S/.test(lines[i]) && !itemRe.test(lines[i])) {
          items[items.length - 1] += (opts.breaks ? '<br/>' : ' ') + lines[i].trim();
          i++;
        }
      } else {
        break;
      }
    }

    var out = '<' + tag + '>';
    items.forEach(function (item) {
      out += '<li>' + inlineRules(esc(item)) + '</li>';
    });
    out += '</' + tag + '>';
    return out + '\n';
  }

  /* ── public API ── */
  global.marked = {
    parse: parse,
    setOptions: setOptions,
    options: opts
  };

})(typeof window !== 'undefined' ? window : this);
