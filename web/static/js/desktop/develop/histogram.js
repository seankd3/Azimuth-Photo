const CHANNELS = [
    { key: 0, color: '#f45b69' },
    { key: 1, color: '#56d68b' },
    { key: 2, color: '#5aa9ff' },
];

export class DevelopHistogram {
    constructor(host) {
        this.host = host;
        this.host.innerHTML = '<div class="develop-hist-wrap">'
            + '<button class="develop-clip develop-clip-shadow" data-tip="Shadow clipping" aria-label="Shadow clipping">◢</button>'
            + '<canvas class="develop-hist" width="288" height="112" aria-label="RGB histogram"></canvas>'
            + '<button class="develop-clip develop-clip-highlight" data-tip="Highlight clipping" aria-label="Highlight clipping">◣</button>'
            + '</div>';
        this.canvas = this.host.querySelector('canvas');
        this.context = this.canvas.getContext('2d');
        this.sampleCanvas = document.createElement('canvas');
        this.sampleContext = this.sampleCanvas.getContext('2d', { willReadFrequently: true });
        this.lastUpdate = 0;
    }

    updateFromRenderer(renderer) {
        const now = performance.now();
        if (now - this.lastUpdate < 120) return;
        this.lastUpdate = now;
        const sourceWidth = renderer.canvas.width;
        const sourceHeight = renderer.canvas.height;
        if (!sourceWidth || !sourceHeight) return;
        const sampleWidth = Math.min(256, sourceWidth);
        const sampleHeight = Math.min(144, sourceHeight);
        this.sampleCanvas.width = sampleWidth;
        this.sampleCanvas.height = sampleHeight;
        this.sampleContext.drawImage(renderer.canvas, 0, 0, sampleWidth, sampleHeight);
        this.update(this.sampleContext.getImageData(0, 0, sampleWidth, sampleHeight).data);
    }

    update(bytes) {
        const bins = CHANNELS.map(() => new Uint32Array(256));
        for (let i = 0; i < bytes.length; i += 4) {
            bins[0][bytes[i]] += 1;
            bins[1][bytes[i + 1]] += 1;
            bins[2][bytes[i + 2]] += 1;
        }
        const peak = Math.max(1, ...bins.flatMap((values) => [...values.slice(1, 255)]));
        const ctx = this.context;
        const { width, height } = this.canvas;
        ctx.clearRect(0, 0, width, height);
        ctx.fillStyle = '#111317';
        ctx.fillRect(0, 0, width, height);
        ctx.globalCompositeOperation = 'screen';
        for (const channel of CHANNELS) {
            ctx.beginPath();
            ctx.moveTo(0, height);
            for (let x = 0; x < width; x += 1) {
                const bin = Math.min(255, Math.floor(x / Math.max(1, width - 1) * 255));
                const y = height - Math.sqrt(bins[channel.key][bin] / peak) * (height - 5);
                ctx.lineTo(x, y);
            }
            ctx.lineTo(width, height);
            ctx.closePath();
            ctx.globalAlpha = .48;
            ctx.fillStyle = channel.color;
            ctx.fill();
        }
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = 'source-over';
        const total = Math.max(1, bytes.length / 4);
        this.host.querySelector('.develop-clip-shadow').classList.toggle('clipped', bins.some((b) => b[0] / total > .01));
        this.host.querySelector('.develop-clip-highlight').classList.toggle('clipped', bins.some((b) => b[255] / total > .01));
    }
}
