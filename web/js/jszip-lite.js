/**
 * jszip-lite.js — Lightweight ZIP parser for browsers
 * API-compatible with JSZip subset used by Legend Portal:
 *   JSZip.loadAsync(data) → Promise<ZipArchive>
 *   zip.forEach((path, entry) => { entry.dir })
 *   zip.file(path).async('arraybuffer') → Promise<ArrayBuffer>
 *
 * Uses browser-native DecompressionStream for deflate.
 * Supports: Store (method 0) and Deflate (method 8).
 */
(function (global) {
  'use strict';

  /* ── binary helpers ── */
  function u16(buf, off) { return buf[off] | (buf[off + 1] << 8); }
  function u32(buf, off) { return (buf[off] | (buf[off + 1] << 8) | (buf[off + 2] << 16) | (buf[off + 3] << 24)) >>> 0; }

  function decodeUTF8(bytes) {
    try { return new TextDecoder('utf-8').decode(bytes); }
    catch (_) {
      // fallback for very old engines
      var s = '';
      for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
      return s;
    }
  }

  /* ── inflate via DecompressionStream ── */
  function inflate(compressed) {
    if (typeof DecompressionStream === 'undefined') {
      return inflateManual(compressed);
    }
    var ds = new DecompressionStream('deflate-raw');
    var writer = ds.writable.getWriter();
    var reader = ds.readable.getReader();
    writer.write(compressed);
    writer.close();

    var chunks = [];
    function pump() {
      return reader.read().then(function (result) {
        if (result.done) {
          if (chunks.length === 1) return chunks[0].buffer;
          var total = 0;
          chunks.forEach(function (c) { total += c.length; });
          var merged = new Uint8Array(total);
          var offset = 0;
          chunks.forEach(function (c) { merged.set(c, offset); offset += c.length; });
          return merged.buffer;
        }
        chunks.push(new Uint8Array(result.value));
        return pump();
      });
    }
    return pump();
  }

  /* ── tiny inflate fallback (fixed + dynamic Huffman) ── */
  function inflateManual(data) {
    var src = new Uint8Array(data);
    var sPos = 0, sBit = 0;
    var dst = [];

    function bits(n) {
      var val = 0;
      for (var i = 0; i < n; i++) {
        val |= ((src[sPos] >> sBit) & 1) << i;
        sBit++;
        if (sBit === 8) { sBit = 0; sPos++; }
      }
      return val;
    }

    function buildTree(lengths) {
      var max = 0;
      for (var i = 0; i < lengths.length; i++) if (lengths[i] > max) max = lengths[i];
      var counts = new Array(max + 1).fill(0);
      for (var i = 0; i < lengths.length; i++) if (lengths[i]) counts[lengths[i]]++;
      var offsets = new Array(max + 1).fill(0);
      for (var i = 1; i < max; i++) offsets[i + 1] = offsets[i] + counts[i];
      var table = new Array(lengths.length).fill(0);
      for (var i = 0; i < lengths.length; i++) {
        if (lengths[i]) { table[offsets[lengths[i]]++] = i; }
      }
      return { counts: counts, table: table, max: max };
    }

    function readSymbol(tree) {
      var code = 0, first = 0, index = 0;
      for (var len = 1; len <= tree.max; len++) {
        code |= bits(1);
        var count = tree.counts[len];
        if (code - first < count) return tree.table[index + (code - first)];
        index += count;
        first = (first + count) << 1;
        code <<= 1;
      }
      return -1;
    }

    // fixed trees
    var fixedLitLen = new Array(288);
    for (var i = 0; i <= 143; i++) fixedLitLen[i] = 8;
    for (var i = 144; i <= 255; i++) fixedLitLen[i] = 9;
    for (var i = 256; i <= 279; i++) fixedLitLen[i] = 7;
    for (var i = 280; i <= 287; i++) fixedLitLen[i] = 8;
    var fixedDist = new Array(32).fill(5);
    var FIXED_LIT = buildTree(fixedLitLen);
    var FIXED_DIST = buildTree(fixedDist);

    var lenBase = [3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258];
    var lenExtra = [0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0];
    var distBase = [1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577];
    var distExtra = [0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13];
    var clOrder = [16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15];

    var bfinal;
    do {
      bfinal = bits(1);
      var btype = bits(2);

      if (btype === 0) {
        // stored
        sBit = 0; sPos = (sBit > 0) ? sPos + 1 : sPos;
        var sLen = u16(src, sPos); sPos += 4;
        for (var k = 0; k < sLen; k++) dst.push(src[sPos++]);
      } else {
        var litTree, distTree;
        if (btype === 1) {
          litTree = FIXED_LIT;
          distTree = FIXED_DIST;
        } else {
          var hlit = bits(5) + 257;
          var hdist = bits(5) + 1;
          var hclen = bits(4) + 4;
          var clLengths = new Array(19).fill(0);
          for (var k = 0; k < hclen; k++) clLengths[clOrder[k]] = bits(3);
          var clTree = buildTree(clLengths);

          var allLengths = [];
          while (allLengths.length < hlit + hdist) {
            var sym = readSymbol(clTree);
            if (sym < 16) { allLengths.push(sym); }
            else if (sym === 16) {
              var rep = bits(2) + 3;
              var prev = allLengths[allLengths.length - 1] || 0;
              for (var r = 0; r < rep; r++) allLengths.push(prev);
            } else if (sym === 17) {
              var rep = bits(3) + 3;
              for (var r = 0; r < rep; r++) allLengths.push(0);
            } else {
              var rep = bits(7) + 11;
              for (var r = 0; r < rep; r++) allLengths.push(0);
            }
          }
          litTree = buildTree(allLengths.slice(0, hlit));
          distTree = buildTree(allLengths.slice(hlit));
        }

        while (true) {
          var sym = readSymbol(litTree);
          if (sym === 256) break;
          if (sym < 256) {
            dst.push(sym);
          } else {
            var li = sym - 257;
            var length = lenBase[li] + bits(lenExtra[li]);
            var di = readSymbol(distTree);
            var distance = distBase[di] + bits(distExtra[di]);
            var start = dst.length - distance;
            for (var k = 0; k < length; k++) dst.push(dst[start + k]);
          }
        }
      }
    } while (!bfinal);

    return Promise.resolve(new Uint8Array(dst).buffer);
  }

  /* ── ZIP entry ── */
  function ZipEntry(name, isDir, method, compressedData, uncompressedSize) {
    this.name = name;
    this.dir = isDir;
    this._method = method;
    this._compressed = compressedData;
    this._uncompressedSize = uncompressedSize;
  }

  ZipEntry.prototype.async = function (type) {
    var self = this;
    if (self.dir) return Promise.resolve(new ArrayBuffer(0));

    var promise;
    if (self._method === 0) {
      // stored
      promise = Promise.resolve(self._compressed.buffer.slice(
        self._compressed.byteOffset,
        self._compressed.byteOffset + self._compressed.byteLength
      ));
    } else if (self._method === 8) {
      // deflated
      promise = inflate(self._compressed);
    } else {
      promise = Promise.reject(new Error('Unsupported compression method: ' + self._method));
    }

    if (type === 'string' || type === 'text') {
      return promise.then(function (ab) { return decodeUTF8(new Uint8Array(ab)); });
    }
    // 'arraybuffer' or default
    return promise;
  };

  /* ── ZIP archive ── */
  function ZipArchive(entries) {
    this._entries = entries; // Map<string, ZipEntry>
  }

  ZipArchive.prototype.forEach = function (callback) {
    var entries = this._entries;
    for (var name in entries) {
      if (entries.hasOwnProperty(name)) {
        callback(name, entries[name]);
      }
    }
  };

  ZipArchive.prototype.file = function (name) {
    var entry = this._entries[name];
    if (!entry) return null;
    return entry;
  };

  /* ── parse ZIP ── */
  function parseZip(buffer) {
    var bytes = new Uint8Array(buffer);
    var entries = {};

    // find End of Central Directory (scan backwards)
    var eocdOff = -1;
    for (var i = bytes.length - 22; i >= 0; i--) {
      if (u32(bytes, i) === 0x06054b50) { eocdOff = i; break; }
    }
    if (eocdOff === -1) throw new Error('Invalid ZIP: EOCD not found');

    var cdOffset = u32(bytes, eocdOff + 16);
    var cdCount = u16(bytes, eocdOff + 10);

    // Check for ZIP64 EOCD locator
    if (cdOffset === 0xFFFFFFFF || cdCount === 0xFFFF) {
      // ZIP64: find ZIP64 EOCD locator
      if (eocdOff >= 20 && u32(bytes, eocdOff - 20) === 0x07064b50) {
        var z64EocdOff = Number(u32(bytes, eocdOff - 12)) + Number(u32(bytes, eocdOff - 8)) * 4294967296;
        if (u32(bytes, z64EocdOff) === 0x06064b50) {
          cdCount = Number(u32(bytes, z64EocdOff + 32)) + Number(u32(bytes, z64EocdOff + 36)) * 4294967296;
          cdOffset = Number(u32(bytes, z64EocdOff + 48)) + Number(u32(bytes, z64EocdOff + 52)) * 4294967296;
        }
      }
    }

    var pos = cdOffset;
    for (var c = 0; c < cdCount; c++) {
      if (u32(bytes, pos) !== 0x02014b50) break;

      var method = u16(bytes, pos + 10);
      var compressedSize = u32(bytes, pos + 20);
      var uncompressedSize = u32(bytes, pos + 24);
      var nameLen = u16(bytes, pos + 28);
      var extraLen = u16(bytes, pos + 30);
      var commentLen = u16(bytes, pos + 32);
      var localHeaderOff = u32(bytes, pos + 42);

      var nameBytes = bytes.subarray(pos + 46, pos + 46 + nameLen);
      var name = decodeUTF8(nameBytes);

      // Parse extra field for ZIP64 sizes
      var extraStart = pos + 46 + nameLen;
      var extraEnd = extraStart + extraLen;
      var ePos = extraStart;
      while (ePos + 4 <= extraEnd) {
        var eId = u16(bytes, ePos);
        var eSize = u16(bytes, ePos + 2);
        if (eId === 0x0001) { // ZIP64 extended info
          var eOff = ePos + 4;
          if (uncompressedSize === 0xFFFFFFFF && eOff + 8 <= ePos + 4 + eSize) {
            uncompressedSize = Number(u32(bytes, eOff)) + Number(u32(bytes, eOff + 4)) * 4294967296;
            eOff += 8;
          }
          if (compressedSize === 0xFFFFFFFF && eOff + 8 <= ePos + 4 + eSize) {
            compressedSize = Number(u32(bytes, eOff)) + Number(u32(bytes, eOff + 4)) * 4294967296;
            eOff += 8;
          }
          if (localHeaderOff === 0xFFFFFFFF && eOff + 8 <= ePos + 4 + eSize) {
            localHeaderOff = Number(u32(bytes, eOff)) + Number(u32(bytes, eOff + 4)) * 4294967296;
          }
        }
        ePos += 4 + eSize;
      }

      var isDir = name.charAt(name.length - 1) === '/';

      // Read from local file header to get actual data offset
      var localNameLen = u16(bytes, localHeaderOff + 26);
      var localExtraLen = u16(bytes, localHeaderOff + 28);
      var dataOff = localHeaderOff + 30 + localNameLen + localExtraLen;

      var compressedData = bytes.subarray(dataOff, dataOff + compressedSize);

      entries[name] = new ZipEntry(name, isDir, method, compressedData, uncompressedSize);

      pos += 46 + nameLen + extraLen + commentLen;
    }

    return new ZipArchive(entries);
  }

  /* ── public API ── */
  function JSZipLite() {}

  JSZipLite.loadAsync = function (data) {
    return Promise.resolve().then(function () {
      if (data instanceof ArrayBuffer) {
        return parseZip(data);
      }
      if (data instanceof Blob || (typeof File !== 'undefined' && data instanceof File)) {
        return new Promise(function (resolve, reject) {
          var reader = new FileReader();
          reader.onload = function () { resolve(parseZip(reader.result)); };
          reader.onerror = function () { reject(reader.error); };
          reader.readAsArrayBuffer(data);
        });
      }
      if (data instanceof Uint8Array) {
        return parseZip(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength));
      }
      throw new Error('Unsupported input type');
    });
  };

  global.JSZip = JSZipLite;

})(typeof window !== 'undefined' ? window : this);
