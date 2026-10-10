import { sdk } from '../sdk'
import { mounts, rootDir } from '../utils'
async function request(effects:any, mode:string, input:object={}) {
  return sdk.SubContainer.withTemp(effects,{imageId:'controller'},mounts,'market-pricing',async sub=>{
    const result=await sub.exec(['python3','/app/market_pricing.py',rootDir,mode],{input:JSON.stringify(input)})
    if(result.exitCode!==0)throw new Error('Pricing settings unavailable. Choose a markup from 0 to 500 basis points.')
    return JSON.parse(String(result.stdout))
  })
}
export const marketPricing=sdk.Action.withInput('market-pricing',async()=>({
  name:'Market Pricing',group:'Swap Setup',description:'Neoxa XBT/BTC order-book pricing. Markup defaults to 0%. Changes apply to new quotes; confirmed amounts stay fixed. No exchange account or trading credentials are used.',
  warning:null,allowedStatuses:'only-running' as const,visibility:'enabled' as const,
}),sdk.InputSpec.of({markupBps:sdk.Value.number({name:'Operator markup (basis points; 100 = 1%)',description:'0 means no additional operator markup. Quotes include the selected coordinator routing fee. Maximum 500 = 5%.',required:true,integer:true,min:0,max:500,default:0,placeholder:null})}),
  async({effects})=>request(effects,'get'),async({effects,input})=>{
    const r=await request(effects,'configure',input)
    return {version:'1' as const,title:'Market Pricing',message:'Saved for new market quotes. Existing quotes retain their original amounts.',result:{type:'single' as const,value:String(r.markupBps/100)+'% operator markup',copyable:false,qr:false,masked:false}}
  })
