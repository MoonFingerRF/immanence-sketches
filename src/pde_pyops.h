// Python semantics the drawing runtime must reproduce bit for bit (the reference is
// python/immanence/pde, CPython 3.13): float32 rounding, min/max with NaN, floor division,
// math.hypot, float %, and list.sort (timsort) -- the last one matters because NaN coordinates
// make the comparison a partial order, where only the exact algorithm gives the same order.
//
// BUILD FLAGS (host and ESP-IDF alike): -O2 -ffp-contract=off, never -ffast-math. Contracting
// a*b+c into an fma changes results (clang on arm64 does it by default; Xtensa gcc has madd.s).
#pragma once
#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

namespace pde {

constexpr int W = 240, H = 128;

enum class Fault : uint8_t { None = 0, Runtime, Memory, Size, Budget, Deadline, Depth };

// Where a fault goes: the first one wins (the Python code raises and abandons the call).
struct FaultRef {
  Fault *f = nullptr;
  const char **msg = nullptr;
  void raise(Fault k, const char *m) const {
    if (*f == Fault::None) {
      *f = k;
      *msg = m;
    }
  }
  bool on() const { return *f != Fault::None; }
};

// math.sin / math.cos: the plain libm calls, one each. Called through a volatile pointer so the
// compiler cannot fuse a sin(t), cos(t) pair into sincos() (clang does, and Apple's __sincos_stret
// differs from sin() in the last bit; Python calls them separately).
inline double py_sin(double x) {
  static double (*volatile f)(double) = static_cast<double (*)(double)>(std::sin);
  return f(x);
}
inline double py_cos(double x) {
  static double (*volatile f)(double) = static_cast<double (*)(double)>(std::cos);
  return f(x);
}

// f32(): Java float rounding (overflow gives +-inf, as jrandom.f32 does).
inline double f32(double x) { return (double)(float)x; }

// Python's min(a, b) / max(a, b): the first argument unless the second is strictly better.
inline double pymin(double a, double b) { return b < a ? b : a; }
inline double pymax(double a, double b) { return b > a ? b : a; }
inline int64_t imin(int64_t a, int64_t b) { return b < a ? b : a; }
inline int64_t imax(int64_t a, int64_t b) { return b > a ? b : a; }

// Python // and % on ints (divisor > 0 or < 0; never 0 here).
inline int64_t floordiv(int64_t a, int64_t b) {
  int64_t q = a / b;
  if ((a % b != 0) && ((a < 0) != (b < 0))) --q;
  return q;
}
inline int64_t floormod(int64_t a, int64_t b) {
  int64_t r = a % b;
  if (r != 0 && ((r < 0) != (b < 0))) r += b;
  return r;
}

// Python float % (float_rem): the result takes the divisor's sign.
inline double pyfmod(double v, double w) {
  double mod = std::fmod(v, w);
  if (mod != 0.0) {
    if ((w < 0) != (mod < 0)) mod += w;
  } else {
    mod = std::copysign(0.0, w);
  }
  return mod;
}

// Python's int(v) for a finite double as a two's complement int64 of its LOW 64 bits (exact for
// any magnitude: the bits Python's big int has there). For bit masks / seeds.
inline uint64_t pyint_bits(double v) {
  double t = std::trunc(v);
  if (std::fabs(t) < 9223372036854775808.0) return (uint64_t)(int64_t)t;
  int e;
  double m = std::frexp(std::fabs(t), &e);           // |t| = m * 2^e, 0.5 <= m < 1
  uint64_t mant = (uint64_t)std::ldexp(m, 53);       // 53-bit integer
  int sh = e - 53;                                   // >= 11 here
  uint64_t low = sh >= 64 ? 0 : (mant << sh);
  return t < 0 ? (uint64_t)(0 - low) : low;
}

// math.hypot(x, y) exactly as CPython 3.12/3.13 computes it (vector_norm, not libm's hypot).
// dl_mul uses Dekker's product (CPython uses fma(); both are exact here, and a soft fma on the
// ESP32's newlib is not trustworthy).
namespace detail {
struct DL { double hi, lo; };
inline DL dl_fast_sum(double a, double b) {
  double x = a + b;
  double y = (a - x) + b;
  return {x, y};
}
inline DL dl_split(double x) {
  double t = x * 134217729.0;
  double hi = t - (t - x);
  return {hi, x - hi};
}
inline DL dl_mul(double x, double y) {
  DL xx = dl_split(x), yy = dl_split(y);
  double p = xx.hi * yy.hi;
  double q = xx.hi * yy.lo + xx.lo * yy.hi;
  double z = p + q;
  double zz = p - z + q + xx.lo * yy.lo;
  return {z, zz};
}
inline double vector_norm2(double *vec, double max, bool found_nan) {
  if (std::isinf(max)) return max;
  if (found_nan) return NAN;
  if (max == 0.0) return max;
  int max_e;
  std::frexp(max, &max_e);
  if (max_e < -1023) {
    const double DMIN = 2.2250738585072014e-308;
    vec[0] /= DMIN;
    vec[1] /= DMIN;
    return DMIN * vector_norm2(vec, max / DMIN, found_nan);
  }
  double scale = std::ldexp(1.0, -max_e);
  double csum = 1.0, frac1 = 0.0, frac2 = 0.0;
  for (int i = 0; i < 2; i++) {
    double x = vec[i] * scale;
    DL pr = dl_mul(x, x);
    DL sm = dl_fast_sum(csum, pr.hi);
    csum = sm.hi;
    frac1 += pr.lo;
    frac2 += sm.lo;
  }
  double h = std::sqrt(csum - 1.0 + (frac1 + frac2));
  DL pr = dl_mul(-h, h);
  DL sm = dl_fast_sum(csum, pr.hi);
  csum = sm.hi;
  frac1 += pr.lo;
  frac2 += sm.lo;
  double x = csum - 1.0 + (frac1 + frac2);
  h += x / (2.0 * h);
  return h / scale;
}
}  // namespace detail

inline double py_hypot(double x, double y) {
  double v[2] = {std::fabs(x), std::fabs(y)};
  double max = 0.0;
  bool nan = false;
  for (double &c : v) {
    nan |= std::isnan(c);
    if (c > max) max = c;
  }
  return detail::vector_norm2(v, max, nan);
}

// ---------------------------------------------------------------------------------------------
// list.sort() of CPython 3.13 (Objects/listobject.c: count_run, binary insertion, powersort
// merging with galloping), for trivially copyable T and a strict "less than" `lt` that may be a
// partial order (NaN). The same sequence of comparisons, so the same permutation.
template <class T, class Lt>
class PySort {
 public:
  void sort(T *a, int n, Lt lt) {
    if (n < 2) return;
    lt_ = &lt;
    base_ = a;
    listlen_ = n;
    npend_ = 0;
    min_gallop_ = MIN_GALLOP;
    T *lo = a;
    int nrem = n;
    int minrun = compute_minrun(n);
    do {
      int r = count_run(lo, nrem);
      if (r < minrun) {
        int force = nrem <= minrun ? nrem : minrun;
        binarysort(lo, force, r);
        r = force;
      }
      found_new_run(r);
      pend_[npend_].base = lo;
      pend_[npend_].len = r;
      ++npend_;
      lo += r;
      nrem -= r;
    } while (nrem);
    while (npend_ > 1) {
      int k = npend_ - 2;
      if (k > 0 && pend_[k - 1].len < pend_[k + 1].len) --k;
      merge_at(k);
    }
  }

 private:
  static constexpr int MIN_GALLOP = 7, MAX_MINRUN = 64;
  struct Run { T *base; int len; int power; };
  const Lt *lt_ = nullptr;
  T *base_ = nullptr;
  int listlen_ = 0, npend_ = 0, min_gallop_ = MIN_GALLOP;
  Run pend_[64];
  std::vector<T> tmp_;

  bool LT(const T &x, const T &y) const { return (*lt_)(x, y); }
  static void reverse(T *lo, T *hi) {
    --hi;
    while (lo < hi) {
      T t = *lo;
      *lo = *hi;
      *hi = t;
      ++lo;
      --hi;
    }
  }
  static int compute_minrun(int n) {
    int r = 0;
    while (n >= MAX_MINRUN) {
      r |= n & 1;
      n >>= 1;
    }
    return n + r;
  }
  void binarysort(T *a, int n, int ok) {
    if (!ok) ++ok;
    for (; ok < n; ++ok) {
      int L = 0, R = ok;
      T pivot = a[ok];
      do {
        int M = (L + R) >> 1;
        if (LT(pivot, a[M])) R = M;
        else L = M + 1;
      } while (L < R);
      for (int M = ok; M > L; --M) a[M] = a[M - 1];
      a[L] = pivot;
    }
  }
  int count_run(T *lo, int nrem) {
    int n;
    for (n = 1; n < nrem; ++n)
      if (LT(lo[n], lo[n - 1])) break;
    if (n == nrem) return n;
    if (n > 1) {
      if (LT(lo[0], lo[n - 1])) return n;
      reverse(lo, lo + n);
    }
    ++n;
    int neq = 0;
    for (; n < nrem; ++n) {
      if (LT(lo[n], lo[n - 1])) {
        if (neq) {
          ++neq;
          reverse(lo + (n - neq), lo + n);
          neq = 0;
        }
      } else {
        if (LT(lo[n - 1], lo[n])) break;
        ++neq;
      }
    }
    if (neq) {
      ++neq;
      reverse(lo + (n - neq), lo + n);
      neq = 0;
    }
    reverse(lo, lo + n);
    for (; n < nrem; ++n)
      if (LT(lo[n], lo[n - 1])) break;
    return n;
  }
  int gallop_left(T key, T *a, int n, int hint) {
    int ofs, lastofs, k;
    a += hint;
    lastofs = 0;
    ofs = 1;
    if (LT(*a, key)) {
      const int maxofs = n - hint;
      while (ofs < maxofs) {
        if (LT(a[ofs], key)) {
          lastofs = ofs;
          ofs = (ofs << 1) + 1;
        } else {
          break;
        }
      }
      if (ofs > maxofs) ofs = maxofs;
      lastofs += hint;
      ofs += hint;
    } else {
      const int maxofs = hint + 1;
      while (ofs < maxofs) {
        if (LT(*(a - ofs), key)) break;
        lastofs = ofs;
        ofs = (ofs << 1) + 1;
      }
      if (ofs > maxofs) ofs = maxofs;
      k = lastofs;
      lastofs = hint - ofs;
      ofs = hint - k;
    }
    a -= hint;
    ++lastofs;
    while (lastofs < ofs) {
      int m = lastofs + ((ofs - lastofs) >> 1);
      if (LT(a[m], key)) lastofs = m + 1;
      else ofs = m;
    }
    return ofs;
  }
  int gallop_right(T key, T *a, int n, int hint) {
    int ofs, lastofs, k;
    a += hint;
    lastofs = 0;
    ofs = 1;
    if (LT(key, *a)) {
      const int maxofs = hint + 1;
      while (ofs < maxofs) {
        if (LT(key, *(a - ofs))) {
          lastofs = ofs;
          ofs = (ofs << 1) + 1;
        } else {
          break;
        }
      }
      if (ofs > maxofs) ofs = maxofs;
      k = lastofs;
      lastofs = hint - ofs;
      ofs = hint - k;
    } else {
      const int maxofs = n - hint;
      while (ofs < maxofs) {
        if (LT(key, a[ofs])) break;
        lastofs = ofs;
        ofs = (ofs << 1) + 1;
      }
      if (ofs > maxofs) ofs = maxofs;
      lastofs += hint;
      ofs += hint;
    }
    a -= hint;
    ++lastofs;
    while (lastofs < ofs) {
      int m = lastofs + ((ofs - lastofs) >> 1);
      if (LT(key, a[m])) ofs = m;
      else lastofs = m + 1;
    }
    return ofs;
  }
  static void mv(T *d, const T *s, int n) {
    if (n > 0) std::memmove((void *)d, (const void *)s, sizeof(T) * (size_t)n);
  }
  T *getmem(int need) {
    if ((int)tmp_.size() < need) tmp_.resize((size_t)need);
    return tmp_.data();
  }
  void merge_lo(T *ssa, int na, T *ssb, int nb) {
    T *tmp = getmem(na);
    mv(tmp, ssa, na);
    T *dest = ssa;
    ssa = tmp;
    *dest++ = *ssb++;
    --nb;
    if (nb == 0) goto Succeed;
    if (na == 1) goto CopyB;
    {
      int min_gallop = min_gallop_;
      for (;;) {
        int acount = 0, bcount = 0;
        for (;;) {
          if (LT(*ssb, *ssa)) {
            *dest++ = *ssb++;
            ++bcount;
            acount = 0;
            --nb;
            if (nb == 0) goto Succeed;
            if (bcount >= min_gallop) break;
          } else {
            *dest++ = *ssa++;
            ++acount;
            bcount = 0;
            --na;
            if (na == 1) goto CopyB;
            if (acount >= min_gallop) break;
          }
        }
        ++min_gallop;
        do {
          min_gallop -= min_gallop > 1;
          min_gallop_ = min_gallop;
          int k = gallop_right(*ssb, ssa, na, 0);
          acount = k;
          if (k) {
            mv(dest, ssa, k);
            dest += k;
            ssa += k;
            na -= k;
            if (na == 1) goto CopyB;
            if (na == 0) goto Succeed;
          }
          *dest++ = *ssb++;
          --nb;
          if (nb == 0) goto Succeed;
          k = gallop_left(*ssa, ssb, nb, 0);
          bcount = k;
          if (k) {
            mv(dest, ssb, k);
            dest += k;
            ssb += k;
            nb -= k;
            if (nb == 0) goto Succeed;
          }
          *dest++ = *ssa++;
          --na;
          if (na == 1) goto CopyB;
        } while (acount >= MIN_GALLOP || bcount >= MIN_GALLOP);
        ++min_gallop;
        min_gallop_ = min_gallop;
      }
    }
  Succeed:
    if (na) mv(dest, ssa, na);
    return;
  CopyB:
    mv(dest, ssb, nb);
    dest[nb] = *ssa;
  }
  void merge_hi(T *ssa, int na, T *ssb, int nb) {
    T *tmp = getmem(nb);
    T *dest = ssb + nb - 1;
    mv(tmp, ssb, nb);
    T *basea = ssa;
    T *baseb = tmp;
    ssb = tmp + nb - 1;
    ssa += na - 1;
    *dest-- = *ssa--;
    --na;
    if (na == 0) goto Succeed;
    if (nb == 1) goto CopyA;
    {
      int min_gallop = min_gallop_;
      for (;;) {
        int acount = 0, bcount = 0;
        for (;;) {
          if (LT(*ssb, *ssa)) {
            *dest-- = *ssa--;
            ++acount;
            bcount = 0;
            --na;
            if (na == 0) goto Succeed;
            if (acount >= min_gallop) break;
          } else {
            *dest-- = *ssb--;
            ++bcount;
            acount = 0;
            --nb;
            if (nb == 1) goto CopyA;
            if (bcount >= min_gallop) break;
          }
        }
        ++min_gallop;
        do {
          min_gallop -= min_gallop > 1;
          min_gallop_ = min_gallop;
          int k = gallop_right(*ssb, basea, na, na - 1);
          k = na - k;
          acount = k;
          if (k) {
            dest -= k;
            ssa -= k;
            mv(dest + 1, ssa + 1, k);
            na -= k;
            if (na == 0) goto Succeed;
          }
          *dest-- = *ssb--;
          --nb;
          if (nb == 1) goto CopyA;
          k = gallop_left(*ssa, baseb, nb, nb - 1);
          k = nb - k;
          bcount = k;
          if (k) {
            dest -= k;
            ssb -= k;
            mv(dest + 1, ssb + 1, k);
            nb -= k;
            if (nb == 1) goto CopyA;
            if (nb == 0) goto Succeed;
          }
          *dest-- = *ssa--;
          --na;
          if (na == 0) goto Succeed;
        } while (acount >= MIN_GALLOP || bcount >= MIN_GALLOP);
        ++min_gallop;
        min_gallop_ = min_gallop;
      }
    }
  Succeed:
    if (nb) mv(dest - (nb - 1), baseb, nb);
    return;
  CopyA:
    mv(dest + 1 - na, ssa + 1 - na, na);
    dest -= na;
    ssa -= na;
    *dest = *ssb;
  }
  void merge_at(int i) {
    T *ssa = pend_[i].base;
    int na = pend_[i].len;
    T *ssb = pend_[i + 1].base;
    int nb = pend_[i + 1].len;
    pend_[i].len = na + nb;
    if (i == npend_ - 3) pend_[i + 1] = pend_[i + 2];
    --npend_;
    int k = gallop_right(*ssb, ssa, na, 0);
    ssa += k;
    na -= k;
    if (na == 0) return;
    nb = gallop_left(ssa[na - 1], ssb, nb, nb - 1);
    if (nb <= 0) return;
    if (na <= nb) merge_lo(ssa, na, ssb, nb);
    else merge_hi(ssa, na, ssb, nb);
  }
  static int powerloop(int s1, int n1, int n2, int n) {
    int result = 0;
    int64_t a = 2 * (int64_t)s1 + n1;
    int64_t b = a + n1 + n2;
    for (;;) {
      ++result;
      if (a >= n) {
        a -= n;
        b -= n;
      } else if (b >= n) {
        break;
      }
      a <<= 1;
      b <<= 1;
    }
    return result;
  }
  void found_new_run(int n2) {
    if (npend_) {
      int s1 = (int)(pend_[npend_ - 1].base - base_);
      int n1 = pend_[npend_ - 1].len;
      int power = powerloop(s1, n1, n2, listlen_);
      while (npend_ > 1 && pend_[npend_ - 2].power > power) merge_at(npend_ - 2);
      pend_[npend_ - 1].power = power;
    }
  }
};

}  // namespace pde
