export class PageCache {
  constructor({ pageSize, load, onPage, onError }) {
    this.pageSize = pageSize;
    this.load = load;
    this.onPage = onPage;
    this.onError = onError;
    this.generation = 0;
    this.reset();
  }

  reset() {
    this.generation += 1;
    this.total = 0;
    this.values = new Map();
    this.loaded = new Set();
    this.pending = new Map();
    this.failed = new Set();
    this.wanted = [0, this.pageSize];
    return this.generation;
  }

  isCurrent(generation) {
    return generation === this.generation;
  }

  seed(generation, items, total, offset = 0) {
    if (!this.isCurrent(generation)) return false;
    this.total = Math.max(0, Number(total));
    this.values = new Map(items.map((item, index) => [offset + index, item]));
    this.loaded.add(this.pageOffset(offset));
    this.onPage(new Map(this.values), this.total);
    return true;
  }

  patch(matches, transform) {
    // Every loaded row the predicate names, not one position: an identity can
    // occupy several rows, and a change to it must land on all of them.
    let touched = 0;
    for (const [position, item] of this.values) {
      if (!matches(item)) continue;
      this.values.set(position, transform(item));
      touched += 1;
    }
    if (touched) this.onPage(new Map(this.values), this.total);
    return touched;
  }

  remove(index) {
    index = Number(index);
    if (!this.values.has(index)) return false;

    const affectedPage = this.pageOffset(index);
    this.generation += 1;
    this.total = Math.max(0, this.total - 1);
    this.values = new Map(
      [...this.values]
        .filter(([position]) => position !== index)
        .map(([position, value]) => [position > index ? position - 1 : position, value]),
    );
    this.loaded = new Set([...this.loaded].filter((offset) => offset < affectedPage));
    this.pending = new Map();
    this.failed = new Set([...this.failed].filter((offset) => offset < affectedPage));
    this.onPage(new Map(this.values), this.total);
    return true;
  }

  refresh(total) {
    // Re-read, in place, the pages around what the window last asked for,
    // and let the others go. The generation and the positions stay, only the
    // rows change, so a selection survives and nothing flashes. A refresh is
    // every worker tick while tiles are made; re-reading every page a long
    // scroll ever loaded was three hundred reads a tick at depth. A page
    // that failed is owed again: the library may be back.
    this.total = Math.max(0, Number(total));
    this.failed = new Set();
    const [from, to] = this.wanted;
    for (const offset of [...this.loaded]) {
      if (offset >= from - this.pageSize && offset < to + this.pageSize) continue;
      this.loaded.delete(offset);
      for (let position = offset; position < offset + this.pageSize; position += 1) this.values.delete(position);
    }
    const generation = this.generation;
    const reads = [...this.loaded].map((offset) => this.load(offset, this.pageSize).then((items) => {
      if (!this.isCurrent(generation)) return;
      for (let position = offset; position < offset + this.pageSize; position += 1) this.values.delete(position);
      for (const [position, item] of items.entries()) this.values.set(offset + position, item);
    }));
    return Promise.all(reads).then(() => {
      if (this.isCurrent(generation)) this.onPage(new Map(this.values), this.total);
    });
  }

  pageOffset(index) {
    return Math.floor(index / this.pageSize) * this.pageSize;
  }

  ensure(index) {
    const offset = this.pageOffset(index);
    if (offset < 0 || offset >= this.total || this.loaded.has(offset) || this.failed.has(offset)) {
      return Promise.resolve();
    }
    if (this.pending.has(offset)) return this.pending.get(offset);

    const generation = this.generation;
    const request = this.load(offset, this.pageSize)
      .then((items) => {
        if (!this.isCurrent(generation)) return;
        for (const [position, item] of items.entries()) this.values.set(offset + position, item);
        this.loaded.add(offset);
        this.onPage(new Map(this.values), this.total);
      })
      .catch((error) => {
        if (!this.isCurrent(generation)) return;
        this.failed.add(offset);
        this.onError(error, offset);
      })
      .finally(() => {
        if (this.pending.get(offset) === request) this.pending.delete(offset);
      });
    this.pending.set(offset, request);
    return request;
  }

  ensureRange(start, end, { look = true } = {}) {
    // What the window is looking at, remembered: the pages a refresh keeps.
    // A read that is not a look (marking a day) leaves that memory alone.
    if (look) this.wanted = [this.pageOffset(start), Math.max(end, start + 1)];
    const requests = [];
    for (let offset = this.pageOffset(start); offset < end; offset += this.pageSize) {
      requests.push(this.ensure(offset));
    }
    return Promise.all(requests);
  }
}
