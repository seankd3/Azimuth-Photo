"""Adobe DNG profile render stages shared by export and the WebGL twin.

The algorithms mirror Adobe DNG SDK 1.7.1 build 2611:
- dng_render.cpp constructs camera -> linear ProPhoto, applies HueSatMap,
  BaselineExposure, LookTable, then the baseline RGB tone curve.
- dng_reference.cpp RefBaselineHueSatMap and RefBaselineRGBTone define the
  interpolation and hue-preserving tone semantics.
- DNG Specification 1.7.1 chapter 6 defines ForwardMatrix and HueSatMap.

This module is deliberately independent of the profile-library implementation.
``resolve_adobe_profile`` is the only dependency seam with adobe_profiles.py.
"""

from __future__ import annotations

import base64
import math
import zlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np


# Exact 1025 float32 samples from dng_tone_curve_acr3_default::Evaluate.
# Binary packing keeps the source reviewable while preserving every SDK value.
_ADOBE_ACR3_DEFAULT_B85 = (
    "c-jqCcU+F^A5M~ygwvMLK$3<+^*-M#X=y7B?Y+nQw%)hiMLCL$2*)O;tYcPIMr4<*$cn7!@KdBHhu{6z^L(EBxj&!#zP{H|P*70l"
    "6nWZdoxErFe3QO??sYGFH(!bU<}(}ZH}5!VKh*f9{TuHO_FAKqc=3F7Ug&LOo>ud0o}|T{_t(A%UW!*XuRnSz&p@z-H@$faPq%ar"
    "uX95+Z+p*qp53}8UR_Zu&#3k-Z}F(FyvPUzPUt?AQwki#sitXgD=YN4!5<B|UI$a|^#LpHnu#4(b#5;AJ9IwRqO^dkYzXGkHbikp"
    "vywPL2%no4B;qX07jv%MQZB-ygfmZG$qgzl<Hj6V&uxFSiAx-@o#Q)Ja3|$eT*0;d9H(}elSEf@XKPMy!qI0qK~_Dt|M4YmqRVxz"
    "`Q%OR+KhYL_X925)G1H6hlkp@i?*-1&c+T-J?tY_*Vn~Gm4D+3ZTq>(7r(f|D}(6%cqO_ZQYNp5!>C}E3hmvfN@ISiktBL7ExVyk"
    "WtLjhx<iL1j?|-}5`9|rzll^GYe>SU#$@k5m3H2mMiuU6RC&#uoL#Nh9&1YQo=tCCY$-0BM}-|kI|Xy;r-Bm&m$^{wg!$xM>p>sf"
    "yvg9DFNF#H$$Cs69Xu3F4W6O&_}_3^vnq<JrpMCw8}VeFltd#HQ|ay2bUJ6lr@ot6v^F}IOnZeirKEribfqNQFDLzROW2tUsdP^f"
    "ZTWX8g_tj+8-il8tu3LDZ_CNlrj!P9R+6-46&>zcO)t&Ls3CbR1yru1_ig25s=a~qeK*qBlE3K0`AsyUXEQ~bY-M-YMmlBN>2|{o"
    "vhLYQ?nV_f#(y`pF8P~$j#SdsXH~Rg=w8aR+DAU&`>Ev519b7wKXjn^Agv!bL}rtYkjVKcWhWh@jM8e-IaEWrw`yt0$K$k0<s`YA"
    "oFZk{)07=sM{8wgD00(TGOIpE2OH1RllFS5>$^akRU62};1Zplb(wz5ze1g%SE)4Z8cmm8C)vsyw0KJ+74B;yo8vcW(}i1X?`^7Y"
    "zC%UN?~?1gdvxa0eR}!z0iF2XOr`&|(3nAwXrodq-BNx`HAA0}`_QNKO8FU?40%q1!EL1TyNz++1*P=9q)T1xbnC+_did%!l|6Yw"
    "{deDz#?^OZai)Vjj=ra>yFZZQhE6(P{4aHiKGL<MPqZ-Tf7I^$ne=CNQP{+8av%AH-uHf?;`Xo9(%3^rwcp5STQ4;i_EAytcY5Rf"
    "gL<s`X^7SUUHvgY#m|1yk8}UgxE;TUll>+Oa|Lv7S3u3kLC6sg!s-u$pb(^ptjmhvSq_Hsj=`8ZLJ5E8DnY+p3Edt;aOuPle4eNb"
    "-BM+2?@`9Yh@tp-bto*%havW_Vfg%O7{m#~5p`=goUBJ+@s<(LP*A~vcok5S3QR3Vg1>$wPJADUfiP8c)vIE);V3-)a}@5q9|fVi"
    "8m=5v!zI<xNX{IM3wK82yu}!#myLm5*BCVVjK#gfW6`Sm2WF@JfxjC6fcI2&3|XcQjo0chcF=(Jb`A7=*Fac+CiISJ!e*ovyb`q#"
    "-=KwudfM2Trwz4x+OU|Wg9Sx8$a<!OavNQ!uGU4{TU{LG#$m~Zaro!sIGD}TL*N!Yw07yC+GRXeZ5xloFXOS<RUby%^by#tk9Ow?"
    "xV(7+_I#Rv<#Q+E^7@JR-7yj6c9Sr9)g+vLF$q<c1}G~sKy$MJv?m)POlXL{YlbM-HbO~~5hSOKz#nFevq8ofzSkH5y~gNwnv9+0"
    "ld-LRGB%k{!3OCRbT&?bgZ5N>ikk|@>Zx%1H5K37O)#+01PZTB5HVvK&gM^p&XsAnt7eL2A*NVgX$tqxrU<s3j{3ssFupk*D>cmU"
    "CBh8H_L?EH%M8or%)s`=Gf;bd2AW2j!!Fny`*)f{@Xj1_%q*agV}WaR7U)p0gtDt8WGgMvd(RRXT2^=$W`z?KR#^7V3W26GA<LYJ"
    "y)`p&r*9^F?5%NEW)0s4YnTkNfufrYp02P#%`F?eQJVz~zgg^#vv9X%78YsE#;f4j49nT5dNLc!#?3)e*c|NMHV03i%|W`JE%t`l"
    "!giZ2nx5L?xUL;sL+oI=*$&x{>=3SLk6jDx@oJqtq<8I6KZ=J%o;-Li=b_^&4^IYjxaq*5Udmxa9mg={u*-_@Zw6t_0fN;#LdzuJ"
    "UKo(G3CMf^#zP0xyE$NNkpmLWIiRkOJuT;=E_E*AtL7r5Z7wvm9ih9x5&A0}VQ|S2@&QLYojH&3a~{Kf9^9VILzTJ{JBt$%ikvX+"
    "j1#7Qae|GBGaRFwv3H|0!^0UmN-lV4>jHbe3%*ymApDsNvd6f>!qpWn5?2hac7;xdE5pSN+kD+H;!ih-PP^glCpY*T%!f|ke7smb"
    "A64h(Bei=zmK(WaUywVlu5ibjbM8>=c1OOU2R;XQAfdzq&+0rF$2~A<f+xCsJsCGV@wUbjwQoJ~RMU&i$_pm>Uf8$a3wNG)!CS=}"
    "x9q*)ndZ&7?~Q>bZxsCW#sO0wED80&qEa7})cN4l2Oo53`$Egjmubxxx2t>+*zAio#RW{u3*ZvDfaziZl+P@{yY~xlRnrf@=J}yb"
    ";0LXpe(<^JhvFZ8Oeg;E_4h}++#iC2{!o1Ek4~k95L+%pT=+sZ*M+b-z7TmY7b0?G0Cvp|KzmF83|0pq_H+O$-voeD3uKrEA~P-!"
    "!`1|%;&dR&Uk75TY7p{f2O%*!h@CqK?#F|$zAXsPhX!McWiSLG!Kf<=hSH&6>}d&RIt#{!$%|0xvj}FQMVPW{5hh(<#5`#cn|}yK"
    "JA`0FQV7D<gdp%_2==#yU|>ins-}nHyMHL$#i96C5sIDH+56{E^k{^k&pr&hW5Q5b5{A8p!*HNE42Sx|aCBlgR9wQLnih_vwc*Tj"
    "!=e8y9QPF>U^Y1dFWn<ho*4m`^%3Yl6@f#~BM_w+iL}X)%s(Shlo5&5>msq`L?qKuB!>Trgt|c#M9xuomJ|i7jKbeXq7ZpM3YWe`"
    "L8cuIJNsz#hDW1%aWpdijz-~?Xl(3=hTn)7Bu<Zk*gFPm_%Rq*8w0i47)V-T@V++&0ot*+Xd4T&&{%Ad#3EvQESk>8V$F+KEL4bt"
    "_M|vGpBsn%=s3o&I6U1Qhwlw>%&X&IH8>tVhVe+77tiJvkN*nek-a+}?=Qw9ygi=XHvu~*BtRVrXb(?7pfrK;E&<(j2{`{a0dM*e"
    "a7;51GMhwH`X}OsAQ7L}BtrdgBJ6G^BC0cy%`*vuCnsUayd?aJN`gd|g#N8bOgBmJdYFW!uSuvFoeZH_GV{@77$zp;M^Q5F>`G=j"
    "O2&i7$=KhUj7MWrplqH3YmXGfB&OhfVG84F3bxm!py^=>{Jx~HOiD$hNh;ntr6M&dmCZaA_co^D$gxzczL|>jj#PLkreWInGz_1U"
    "h7P|p=%=UQzv47l?@mL?*)&u<Ok-J-1~rv*>@Z443#B72I2~KF(lKpiI!;%m!>v9Yt*z;(?@ni$%)oP_3|!$da4;}~@gf7_k_^OD"
    "WMF<>1}q<Bfd6j>f|N4xmtH0wS!d$BS0;YNXR<uWM9YRuhF2yIUdhDA=b2dgEfeEZ`3N=U<13Gk*ZzEnQ~AvQ_&BhIk1faeSaF>X"
    "gO}`EA0LXN1o&bkVEhqanx6o=$pXa61^8!!09y|V@VY?&)5ikjd=}vBkSv6a%VJrWg-@<oSQVay8QEF5U6RGH%)-gqEF8U&1>YB0"
    "=<dlv=kRQ-n2?Pk>ueahXG1SC8yY#;P+gwQx=A*Is<YvHEgKb2v$44=n|VVH>~(TrJtGHyI_BVEU=HLdIV^v2FsVETjeByK_vYa8"
    "?Hr`P%7JEY4lWGOMe_JujJ3?g8K+!^buJQ8bD<{B#preHdsQy}IF$?ard(*Y<wC1Fm-%!abhYx3W0HqlULNjv=V5Ae9;W5wp=eni"
    "o^Q&-tbg+G{%jr`@8q%k&V$$2JnS7JWEm%fiir^C?1a$t65@J<5ce~MSh_@rS>-}FR0{FsxR7-!Ar7|+nU@OD&@aSI6%p$$BD^#c"
    "p_4@L^bsK_N`%vV5iTte!FZhr#k)mVS0h5tB@y#m5yrd`A-hL}7NvZYspqpf<>QcbKGdA^(YP=lOXKoc(B(6}=cBefpXojyCN=r6"
    "Z^&oao{!<}`MA`TkB7hW@nKW}evU7|D6;}g;uSC*6tL_qz@p>=ToxAK(Xs+KZYV%NWdSZ!7r^vl0XE(#z@WAQq<v)T{cL@>7(=wg"
    ";2VoEbEX(O=86&RBZhIf7-gwq_~(nESuDom^<r$>Eyk%MVu;U)S#K00q*aXZ@5Gq%Rm^-vf=pEjN_8dJGev?1YYFoh34VA>pdKb+"
    "eM-VQs{~qw5?HNa|7?{&cb|l1g#`OAN}zXJg0d$PguRpC?iUH>{gyDjNl~OFWqu?@gSix|?WG8Dm145Ll=UPj&ZkJRB2S9ILMh7^"
    "DL!qMGHyuGc2o+*vr^o?#{Ro6WgL;h?t>H&JyI0@mSWEc85%WYtTW4yYAVB;Su)hjmBGSO#`G$~-DnxhQyFgL$>6+1hKr>#&_)^R"
    "cF7orWKcOSLvg(fN{uqKK9C`xO$L+qGW_b6;r@UOKFV?!j+W!QjvTiP<@hmOj@z^3_}f8_JM-nt7v-$a$WfIbXFQYRw@8lWLOB#x"
    "%3-!aj*uO46z`Rz_NW}Tr{%08%dxIW4y|T6oSw^Z<E<RM|C2-9$Mz^J#{U7hb(lE"
)
ADOBE_ACR3_DEFAULT_TONE_CURVE = np.frombuffer(
    zlib.decompress(base64.b85decode(_ADOBE_ACR3_DEFAULT_B85)),
    dtype="<f4",
).copy()
ADOBE_ACR3_DEFAULT_TONE_CURVE.setflags(write=False)

D50_XYZ = np.array([0.9642, 1.0, 0.8249], dtype=np.float64)
PROPHOTO_TO_XYZ_D50 = np.array(
    [[0.7977, 0.1352, 0.0313], [0.2880, 0.7119, 0.0001], [0.0, 0.0, 0.8249]],
    dtype=np.float64,
)
# dng_color_space::SetMatrixToPCS scales rounded primaries to map white exactly.
PROPHOTO_TO_XYZ_D50 *= (D50_XYZ / PROPHOTO_TO_XYZ_D50.sum(axis=1))[:, None]
XYZ_D50_TO_PROPHOTO = np.linalg.inv(PROPHOTO_TO_XYZ_D50).astype(np.float32)
XYZ_D50_TO_SRGB = np.array(
    [[3.1338561, -1.6168667, -0.4906146],
     [-0.9787684, 1.9161415, 0.0334540],
     [0.0719453, -0.2289914, 1.4052427]],
    dtype=np.float32,
)
PROPHOTO_TO_LINEAR_SRGB = (XYZ_D50_TO_SRGB @ PROPHOTO_TO_XYZ_D50).astype(np.float32)

ILLUMINANT_CCT = {
    17: 2856.0,  # Standard light A
    18: 4874.0,  # Standard light B
    19: 6774.0,  # Standard light C
    20: 5503.0,  # D55
    21: 6504.0,  # D65
    22: 7504.0,  # D75
    23: 5003.0,  # D50
}
SRGB_LINEAR_THRESHOLD = np.float32(0.0031308)
SRGB_DECODE_THRESHOLD = np.float32(0.04045)


def resolve_adobe_profile(image_meta: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Resolve embedded/library Adobe data without coupling this lane to its loader."""
    if not isinstance(image_meta, Mapping):
        return None
    embedded = image_meta.get("adobe_profile")
    if isinstance(embedded, Mapping):
        return normalize_adobe_profile(embedded)
    try:
        from .adobe_profiles import resolve_adobe_profile as loader
    except (ImportError, ModuleNotFoundError):
        return None
    profile = loader(
        image_meta.get("filepath") or image_meta.get("path"),
        image_meta.get("camera_model") or image_meta.get("UniqueCameraModel") or "",
    )
    return normalize_adobe_profile(profile) if isinstance(profile, Mapping) else None


def normalize_adobe_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten the DNGPROF loader's grouped matrix/illuminant representation."""
    result = dict(profile)
    matrices = profile.get("matrices")
    if isinstance(matrices, Mapping):
        for name in ("color_matrix1", "color_matrix2", "forward_matrix1", "forward_matrix2"):
            if result.get(name) is None and matrices.get(name) is not None:
                result[name] = matrices[name]
    illuminants = profile.get("illuminants")
    if isinstance(illuminants, Mapping):
        for name in ("calibration_illuminant1", "calibration_illuminant2"):
            if result.get(name) is None and illuminants.get(name) is not None:
                result[name] = illuminants[name]
    return result


def _finite_number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _matrix(profile: Mapping[str, Any], *names: str) -> np.ndarray | None:
    for name in names:
        value = profile.get(name)
        if value is None:
            continue
        try:
            matrix = np.asarray(value, dtype=np.float64).reshape(3, 3)
        except (TypeError, ValueError):
            continue
        if np.all(np.isfinite(matrix)):
            return matrix
    return None


def illuminant_temperature(value: Any, fallback: float) -> float:
    number = _finite_number(value, fallback)
    if int(number) in ILLUMINANT_CCT:
        return ILLUMINANT_CCT[int(number)]
    return number if number > 1000.0 else fallback


def dual_illuminant_weight(
    cct: float,
    illuminant1: Any = 17,
    illuminant2: Any = 21,
) -> float:
    """Return map/matrix 1 weight using the SDK's reciprocal-temperature rule."""
    t1 = illuminant_temperature(illuminant1, 2856.0)
    t2 = illuminant_temperature(illuminant2, 6504.0)
    if t1 <= 0.0 or t2 <= 0.0 or t1 == t2:
        return 1.0
    reverse = t1 > t2
    lo, hi = (t2, t1) if reverse else (t1, t2)
    temperature = max(_finite_number(cct, 5500.0), 1.0)
    if temperature <= lo:
        weight = 1.0
    elif temperature >= hi:
        weight = 0.0
    else:
        weight = ((1.0 / temperature) - (1.0 / hi)) / ((1.0 / lo) - (1.0 / hi))
    return float(1.0 - weight if reverse else weight)


def interpolated_forward_matrix(profile: Mapping[str, Any], cct: float) -> np.ndarray | None:
    first = _matrix(profile, "forward_matrix1", "forward_matrix")
    second = _matrix(profile, "forward_matrix2")
    if first is None:
        return second.astype(np.float32) if second is not None else None
    if second is None:
        return first.astype(np.float32)
    weight = dual_illuminant_weight(
        cct, profile.get("calibration_illuminant1", 17), profile.get("calibration_illuminant2", 21)
    )
    return (weight * first + (1.0 - weight) * second).astype(np.float32)


def camera_rgb_to_prophoto(
    camera_rgb: np.ndarray,
    profile: Mapping[str, Any],
    cct: float,
    *,
    reference_neutral: Sequence[float] | None = None,
) -> np.ndarray:
    """DNG stage 2+3: reference-neutral camera RGB -> XYZ D50 -> ProPhoto."""
    fm = interpolated_forward_matrix(profile, cct)
    if fm is None:
        raise ValueError("Adobe profile has no ForwardMatrix")
    camera = np.maximum(np.asarray(camera_rgb, dtype=np.float32), 0.0)
    neutral = reference_neutral
    if neutral is None:
        neutral = profile.get("as_shot_neutral")
    if neutral is not None:
        n = np.asarray(neutral, dtype=np.float32).reshape(-1)[:3]
        if n.shape == (3,) and np.all(np.isfinite(n)) and np.all(n > 0.0):
            camera = camera / n
    matrix = XYZ_D50_TO_PROPHOTO @ fm
    return np.ascontiguousarray(camera.reshape(-1, 3) @ matrix.T).reshape(camera.shape).astype(np.float32)


def linear_srgb_to_prophoto(linear_srgb: np.ndarray) -> np.ndarray:
    """Convert an existing LibRaw/lossydng linear-sRGB base to linear ProPhoto."""
    matrix = XYZ_D50_TO_PROPHOTO @ np.linalg.inv(XYZ_D50_TO_SRGB)
    rgb = np.asarray(linear_srgb, dtype=np.float32)
    return np.ascontiguousarray(rgb.reshape(-1, 3) @ matrix.T).reshape(rgb.shape).astype(np.float32)


def apply_baseline_exposure(
    linear_prophoto: np.ndarray,
    profile: Mapping[str, Any] | None,
    *,
    file_baseline_exposure: float | None = None,
) -> np.ndarray:
    """DNG stage 4: file BaselineExposure plus the active profile's offset."""
    if file_baseline_exposure is None:
        exposure = _finite_number((profile or {}).get("baseline_exposure"), 0.0)
    else:
        exposure = _finite_number(file_baseline_exposure, 0.0)
    # DNG 1.7.1 BaselineExposureOffset is profile-scoped and additive.  This
    # remains active when a LinearRaw base already includes the per-file tag.
    exposure += _finite_number((profile or {}).get("baseline_exposure_offset"), 0.0)
    return np.asarray(linear_prophoto, dtype=np.float32) * np.float32(np.exp2(exposure))


def _rgb_to_hsv6(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    # The v1 profile path is SDR (dng_render supportOverrange=false): its
    # camera-to-ProPhoto stage pins every component before the HSV table.
    pinned = np.clip(np.asarray(rgb, dtype=np.float32), 0.0, 1.0)
    r, g, b = pinned[..., 0], pinned[..., 1], pinned[..., 2]
    value = np.maximum(r, np.maximum(g, b))
    minimum = np.minimum(r, np.minimum(g, b))
    gap = value - minimum
    nonzero = gap > 0.0
    safe_gap = np.where(nonzero, gap, 1.0)
    hue = np.zeros_like(value)
    red = nonzero & (r == value)
    green = nonzero & ~red & (g == value)
    blue = nonzero & ~red & ~green
    hue = np.where(red, np.mod((g - b) / safe_gap, 6.0), hue)
    hue = np.where(green, 2.0 + (b - r) / safe_gap, hue)
    hue = np.where(blue, 4.0 + (r - g) / safe_gap, hue)
    saturation = np.where(nonzero, gap / np.maximum(value, np.float32(1e-20)), 0.0)
    return hue.astype(np.float32), saturation.astype(np.float32), value.astype(np.float32)


def _hsv6_to_rgb(hue: np.ndarray, saturation: np.ndarray, value: np.ndarray) -> np.ndarray:
    h = np.mod(hue, 6.0)
    i = np.floor(h).astype(np.int32)
    f = h - i
    p = value * (1.0 - saturation)
    q = value * (1.0 - saturation * f)
    t = value * (1.0 - saturation * (1.0 - f))
    choices = (
        np.stack((value, t, p), axis=-1),
        np.stack((q, value, p), axis=-1),
        np.stack((p, value, t), axis=-1),
        np.stack((p, q, value), axis=-1),
        np.stack((t, p, value), axis=-1),
        np.stack((value, p, q), axis=-1),
    )
    result = np.zeros(choices[0].shape, dtype=np.float32)
    for sector, choice in enumerate(choices):
        result = np.where((i == sector)[..., None], choice, result)
    return result


def _srgb_encode(value: np.ndarray) -> np.ndarray:
    x = np.clip(value, 0.0, 1.0)
    return np.where(x <= SRGB_LINEAR_THRESHOLD, 12.92 * x, 1.055 * np.power(x, 1.0 / 2.4) - 0.055)


def _srgb_decode(value: np.ndarray) -> np.ndarray:
    x = np.clip(value, 0.0, 1.0)
    return np.where(x <= SRGB_DECODE_THRESHOLD, x / 12.92, np.power((x + 0.055) / 1.055, 2.4))


def _table_payload(table: Mapping[str, Any], cct: float, profile: Mapping[str, Any]) -> tuple[np.ndarray, tuple[int, int, int]]:
    dims_raw = table.get("dims") or profile.get("profile_hue_sat_map_dims")
    if not isinstance(dims_raw, Sequence) or len(dims_raw) != 3:
        raise ValueError("DNG HSV table dims must be [hue, saturation, value]")
    dims = tuple(int(x) for x in dims_raw)
    if dims[0] < 1 or dims[1] < 2 or dims[2] < 1:
        raise ValueError(f"invalid DNG HSV table dims: {dims}")
    first = table.get("data1")
    second = table.get("data2")
    data = table.get("data")
    if first is None:
        first = data
    expected = dims[0] * dims[1] * dims[2] * 3
    a = np.asarray(first, dtype=np.float32).reshape(-1)
    if a.size != expected:
        raise ValueError(f"DNG HSV table has {a.size} values; expected {expected}")
    if second is not None:
        b = np.asarray(second, dtype=np.float32).reshape(-1)
        if b.size == expected:
            weight = dual_illuminant_weight(
                cct, profile.get("calibration_illuminant1", 17), profile.get("calibration_illuminant2", 21)
            )
            a = np.float32(weight) * a + np.float32(1.0 - weight) * b
    # File order is value-major, then hue, then saturation.
    return a.reshape(dims[2], dims[0], dims[1], 3), dims


def apply_hsv_delta_table(
    linear_prophoto: np.ndarray,
    table: Mapping[str, Any] | None,
    *,
    cct: float,
    profile: Mapping[str, Any],
    encoding: int = 0,
) -> np.ndarray:
    """DNG stages 5/6: circular hue + clamped saturation/value interpolation."""
    if not isinstance(table, Mapping):
        return np.asarray(linear_prophoto, dtype=np.float32)
    data, (hue_count, sat_count, val_count) = _table_payload(table, cct, profile)
    hue, saturation, value = _rgb_to_hsv6(linear_prophoto)
    encoded_value = _srgb_encode(value) if int(encoding) != 0 else value

    hp = hue * np.float32(hue_count / 6.0)
    sp = saturation * np.float32(sat_count - 1)
    vp = encoded_value * np.float32(val_count - 1)
    h0_raw = np.floor(hp).astype(np.int64)
    h0 = np.mod(h0_raw, hue_count)
    h1 = np.mod(h0 + 1, hue_count)
    s0 = np.clip(np.floor(sp).astype(np.int64), 0, sat_count - 2)
    s1 = s0 + 1
    if val_count > 1:
        v0 = np.clip(np.floor(vp).astype(np.int64), 0, val_count - 2)
        v1 = v0 + 1
        vf = (vp - v0).astype(np.float32)
    else:
        v0 = np.zeros_like(h0)
        v1 = v0
        vf = np.zeros_like(hue)

    hf = (hp - h0_raw).astype(np.float32)
    sf = (sp - s0).astype(np.float32)
    result = np.zeros(hue.shape + (3,), dtype=np.float32)
    value_samples = ((v0, np.ones_like(vf)),) if val_count == 1 else ((v0, 1.0 - vf), (v1, vf))
    for vi, vw in value_samples:
        for hi, hw in ((h0, 1.0 - hf), (h1, hf)):
            result += data[vi, hi, s0] * (vw * hw * (1.0 - sf))[..., None]
            result += data[vi, hi, s1] * (vw * hw * sf)[..., None]

    hue = hue + result[..., 0] * np.float32(6.0 / 360.0)
    saturation = np.minimum(saturation * result[..., 1], 1.0)
    encoded_value = np.clip(encoded_value * result[..., 2], 0.0, 1.0)
    value = _srgb_decode(encoded_value) if int(encoding) != 0 else encoded_value
    return _hsv6_to_rgb(hue, saturation, value)


def tone_curve_lut(profile_tone_curve: Any = None) -> np.ndarray:
    """Return a sampled profile curve, or the exact SDK ACR3 default table."""
    if profile_tone_curve is None:
        return ADOBE_ACR3_DEFAULT_TONE_CURVE
    try:
        points = np.asarray(profile_tone_curve, dtype=np.float32)
    except (TypeError, ValueError):
        return ADOBE_ACR3_DEFAULT_TONE_CURVE
    if points.ndim == 1 and points.size % 2 == 0:
        points = points.reshape(-1, 2)
    if points.ndim != 2 or points.shape[1] != 2 or points.shape[0] < 2:
        return ADOBE_ACR3_DEFAULT_TONE_CURVE
    order = np.argsort(points[:, 0], kind="stable")
    points = points[order].astype(np.float64)
    if np.any(np.diff(points[:, 0]) <= 0.0):
        return ADOBE_ACR3_DEFAULT_TONE_CURVE
    count = points.shape[0]
    slopes = np.zeros(count, dtype=np.float64)
    width = points[1, 0] - points[0, 0]
    segment_slope = (points[1, 1] - points[0, 1]) / width
    slopes[0] = segment_slope
    for index in range(2, count):
        next_width = points[index, 0] - points[index - 1, 0]
        next_slope = (points[index, 1] - points[index - 1, 1]) / next_width
        slopes[index - 1] = (segment_slope * next_width + next_slope * width) / (width + next_width)
        width, segment_slope = next_width, next_slope
    slopes[-1] = 2.0 * segment_slope - slopes[-2]
    slopes[0] = 2.0 * slopes[0] - slopes[1]
    if count > 2:
        lower = np.zeros(count, dtype=np.float64)
        upper = np.zeros(count, dtype=np.float64)
        solution = np.zeros(count, dtype=np.float64)
        upper[0] = 0.5
        lower[-1] = 0.5
        solution[0] = 0.75 * (slopes[0] + slopes[1])
        solution[-1] = 0.75 * (slopes[-2] + slopes[-1])
        for index in range(1, count - 1):
            span = 2.0 * (points[index + 1, 0] - points[index - 1, 0])
            lower[index] = (points[index + 1, 0] - points[index, 0]) / span
            upper[index] = (points[index, 0] - points[index - 1, 0]) / span
            solution[index] = 1.5 * slopes[index]
        for index in range(1, count):
            divisor = 1.0 - upper[index - 1] * lower[index]
            if index != count - 1:
                upper[index] /= divisor
            solution[index] = (solution[index] - solution[index - 1] * lower[index]) / divisor
        for index in range(count - 2, -1, -1):
            solution[index] -= upper[index] * solution[index + 1]
        slopes = solution
    x = np.linspace(0.0, 1.0, ADOBE_ACR3_DEFAULT_TONE_CURVE.size, dtype=np.float32)
    segment = np.searchsorted(points[:, 0], x, side="left").clip(1, count - 1)
    x0, x1 = points[segment - 1, 0], points[segment, 0]
    y0, y1 = points[segment - 1, 1], points[segment, 1]
    s0, s1 = slopes[segment - 1], slopes[segment]
    width = x1 - x0
    b = (x - x0) / width
    c = (x1 - x) / width
    sampled = ((y0 * (2.0 - c + b) + s0 * width * b) * c * c
               + (y1 * (2.0 - b + c) - s1 * width * c) * b * b)
    sampled = np.where(x <= points[0, 0], points[0, 1], sampled)
    sampled = np.where(x >= points[-1, 0], points[-1, 1], sampled)
    return sampled.astype(np.float32)


def _sample_lut(value: np.ndarray, lut: np.ndarray) -> np.ndarray:
    clipped = np.clip(value, 0.0, 1.0)
    position = clipped * np.float32(lut.size - 1)
    low = np.floor(position).astype(np.int64)
    high = np.minimum(low + 1, lut.size - 1)
    fraction = position - low
    return lut[low] * (1.0 - fraction) + lut[high] * fraction


def apply_rgb_ratio_tone(linear_prophoto: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """SDK RefBaselineRGBTone: curve extrema, preserve middle-channel ratio."""
    rgb = np.clip(np.asarray(linear_prophoto, dtype=np.float32), 0.0, 1.0)
    minimum = np.min(rgb, axis=-1)
    maximum = np.max(rgb, axis=-1)
    curved_min = _sample_lut(minimum, lut)
    curved_max = _sample_lut(maximum, lut)
    span = maximum - minimum
    ratio = np.divide(
        rgb - minimum[..., None],
        span[..., None],
        out=np.zeros_like(rgb),
        where=span[..., None] > 0.0,
    )
    result = curved_min[..., None] + (curved_max - curved_min)[..., None] * ratio
    gray = span <= 0.0
    return np.where(gray[..., None], curved_min[..., None], result).astype(np.float32)


def prophoto_to_display_srgb(linear_prophoto: np.ndarray) -> np.ndarray:
    linear = np.asarray(linear_prophoto, dtype=np.float32)
    srgb = np.ascontiguousarray(linear.reshape(-1, 3) @ PROPHOTO_TO_LINEAR_SRGB.T).reshape(linear.shape)
    return _srgb_encode(np.clip(srgb, 0.0, 1.0)).astype(np.float32)


def prepare_scene_linear(
    linear_rgb: np.ndarray,
    profile: Mapping[str, Any],
    *,
    cct: float,
    input_space: str = "linear_srgb",
    reference_neutral: Sequence[float] | None = None,
    file_baseline_exposure: float | None = None,
) -> np.ndarray:
    """Stages 2-4. This is the exact scene-linear tap consumed by film."""
    if input_space == "camera":
        prophoto = camera_rgb_to_prophoto(linear_rgb, profile, cct, reference_neutral=reference_neutral)
    elif input_space == "prophoto":
        prophoto = np.asarray(linear_rgb, dtype=np.float32)
    elif input_space == "linear_srgb":
        prophoto = linear_srgb_to_prophoto(linear_rgb)
    else:
        raise ValueError(f"unsupported DNG input space: {input_space}")
    return apply_baseline_exposure(prophoto, profile, file_baseline_exposure=file_baseline_exposure)


def apply_adobe_style(
    scene_linear_prophoto: np.ndarray,
    profile: Mapping[str, Any],
    *,
    cct: float,
    user_ops: Callable[[np.ndarray], np.ndarray] | None = None,
) -> np.ndarray:
    """Stages 5-8; user operations interleave after LookTable and before tone."""
    result = apply_hsv_delta_table(
        scene_linear_prophoto,
        profile.get("hue_sat_map"),
        cct=cct,
        profile=profile,
        encoding=int(profile.get("hue_sat_map_encoding") or 0),
    )
    result = apply_hsv_delta_table(
        result,
        profile.get("look_table"),
        cct=cct,
        profile=profile,
        encoding=int(profile.get("look_table_encoding") or 0),
    )
    if user_ops is not None:
        result = np.asarray(user_ops(result), dtype=np.float32)
    lut = tone_curve_lut(profile.get("tone_curve"))
    result = apply_rgb_ratio_tone(result, lut)
    return prophoto_to_display_srgb(result)


def render_dng_profile(
    linear_rgb: np.ndarray,
    profile: Mapping[str, Any],
    *,
    cct: float,
    input_space: str = "linear_srgb",
    reference_neutral: Sequence[float] | None = None,
    file_baseline_exposure: float | None = None,
    user_ops: Callable[[np.ndarray], np.ndarray] | None = None,
) -> np.ndarray:
    """Render DNG stages 2-8 with the frozen §30.2 ordering."""
    profile = normalize_adobe_profile(profile)
    scene = prepare_scene_linear(
        linear_rgb,
        profile,
        cct=cct,
        input_space=input_space,
        reference_neutral=reference_neutral,
        file_baseline_exposure=file_baseline_exposure,
    )
    return apply_adobe_style(scene, profile, cct=cct, user_ops=user_ops)
