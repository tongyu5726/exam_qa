# 信号与系统 · 公式专项

本文件是 data/knowledge/08-信号与系统-公式专项.md 的固定内容快照，供重复评估使用。

## 连续时间傅里叶变换
角频率形式的正变换定义为：
$$X(j\omega)=\int_{-\infty}^{\infty}x(t)e^{-j\omega t}\,dt$$
逆变换：
$$x(t)=\frac{1}{2\pi}\int_{-\infty}^{\infty}X(j\omega)e^{j\omega t}\,d\omega$$

## 卷积
$$(x*h)(t)=\int_{-\infty}^{\infty}x(\tau)h(t-\tau)\,d\tau$$
LTI 系统的零状态响应等于输入与冲激响应的卷积。

## 采样定理
若 x(t) 的频谱在 |ω| > ω_m 为零，则采样角频率须满足 ω_s > 2ω_m。
本样例称 2ω_m 为奈奎斯特率；等于该值时属于临界情况，实践中需留保护带。
