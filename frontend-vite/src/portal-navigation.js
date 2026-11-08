export function portalModuleForRoute(path,catalog){
 return catalog.find(item=>item.route===path||item.routes?.includes(path))?.key
  ||(path.startsWith('/extensions/')?path.split('/').at(-1):null)
}
