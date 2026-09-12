// Counting things by name, one way: "1 photograph", "1,234 photographs",
// "1 person", "3 people". Every surface that says how many says it through
// here, so no line ever reads "1 photographs".

export function count(n, one, many = `${one}s`) {
  const number = Number(n) || 0;
  return `${number.toLocaleString()} ${number === 1 ? one : many}`;
}
