export function accountDisplay(data: {
  accounts: readonly { id: string; name: string }[];
}, id: string) {
  const account = data.accounts.find(item => item.id === id);
  if (!account) return id;
  return data.accounts.filter(item => item.name === account.name).length > 1
    ? `${account.name} (${id})` : account.name;
}
