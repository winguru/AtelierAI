/**
 * Minimal BlurHash decoder (~2.5KB).
 *
 * Based on the reference algorithm by Dag Ågren / Wolt.
 * Exposes `BlurHashDecode.decode(hash, width, height)` → Uint8ClampedArray (RGBA).
 *
 * @see https://github.com/woltapp/blurhash
 */
(function (root) {
  'use strict';

  var chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~';

  var base83 = {
    decode: function (str) {
      var value = 0;
      for (var i = 0; i < str.length; i++) {
        value = value * 83 + chars.indexOf(str[i]);
      }
      return value;
    }
  };

  function pow2(n) { return Math.pow(n, 2); }
  function signPow(val, exp) { return Math.sign(val) * Math.pow(Math.abs(val), exp); }
  function sRGBToLinear(value) {
    var v = value / 255;
    return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  }
  function linearToSRGB(value) {
    var v = Math.max(0, Math.min(1, value));
    return v <= 0.0031308 ? Math.round(v * 12.92 * 255 + 0.5) : Math.round((1.055 * Math.pow(v, 1.0 / 2.4) - 0.055) * 255 + 0.5);
  }

  function decodeDC(value) {
    return [
      sRGBToLinear((value >> 16)),
      sRGBToLinear(((value >> 8) & 255)),
      sRGBToLinear((value & 255))
    ];
  }

  function decodeAC(value, maxAC) {
    var qR = Math.floor(value / (19 * 19));
    var qG = Math.floor(value / 19) % 19;
    var qB = value % 19;
    return [
      signPow((qR - 9) / 9, 2.0) * maxAC,
      signPow((qG - 9) / 9, 2.0) * maxAC,
      signPow((qB - 9) / 9, 2.0) * maxAC
    ];
  }

  function decode(hash, width, height, punch) {
    punch = punch || 1;

    if (typeof hash !== 'string' || hash.length < 6) return null;

    var sizeFlag = base83.decode(hash[0]);
    var sizeY = Math.floor(sizeFlag / 9) + 1;
    var sizeX = (sizeFlag % 9) + 1;

    var quantMaxVal = base83.decode(hash[1]);
    var maxAC = (quantMaxVal + 1) / 166;

    var colors = [];
    var pos = 2;
    for (var j = 0; j < sizeY; j++) {
      for (var i = 0; i < sizeX; i++) {
        if (i === 0 && j === 0) {
          var dcVal = base83.decode(hash.substring(pos, pos + 4));
          colors.push(decodeDC(dcVal));
          pos += 4;
        } else {
          var acVal = base83.decode(hash.substring(pos, pos + 2));
          colors.push(decodeAC(acVal, maxAC * punch));
          pos += 2;
        }
      }
    }

    // Validate hash length
    if (pos !== hash.length) return null;

    var pixelsPerRow = width * 4;
    var numPixels = width * height;
    var data = new Uint8ClampedArray(numPixels * 4);

    for (var y = 0; y < height; y++) {
      for (var x = 0; x < width; x++) {
        var r = 0, g = 0, b = 0;
        for (var j2 = 0; j2 < sizeY; j2++) {
          for (var i2 = 0; i2 < sizeX; i2++) {
            var basis = Math.cos((Math.PI * x * i2) / width) * Math.cos((Math.PI * y * j2) / height);
            var color = colors[j2 * sizeX + i2];
            r += color[0] * basis;
            g += color[1] * basis;
            b += color[2] * basis;
          }
        }
        var idx = y * pixelsPerRow + x * 4;
        data[idx] = linearToSRGB(r);
        data[idx + 1] = linearToSRGB(g);
        data[idx + 2] = linearToSRGB(b);
        data[idx + 3] = 255;
      }
    }
    return data;
  }

  var lib = { decode: decode };
  if (typeof module !== 'undefined' && module.exports) { module.exports = lib; }
  else { root.BlurHashDecode = lib; }
})(typeof window !== 'undefined' ? window : this);
