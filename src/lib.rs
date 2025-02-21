use solana_program::{
    account_info::{next_account_info, AccountInfo},
    entrypoint,
    entrypoint::ProgramResult,
    msg,
    pubkey::Pubkey,
    program_error::ProgramError,
};

entrypoint!(process_instruction);

pub fn process_instruction(
    program_id: &Pubkey,
    accounts: &[AccountInfo],
    _instruction_data: &[u8],
) -> ProgramResult {
    let accounts_iter = &mut accounts.iter();
    let user_account = next_account_info(accounts_iter)?;

    if !user_account.is_signer {
        return Err(ProgramError::MissingRequiredSignature);
    }

    let jupiter_price = 100.0;
    let gmgn_price = 102.0;
    let moonshot_price = 101.5;
    let fee = 0.0005;

    if moonshot_price > jupiter_price + fee {
        msg!("Buy Jupiter (${}), Sell Moonshot (${})", jupiter_price, moonshot_price);
    } else if jupiter_price > moonshot_price + fee {
        msg!("Buy Moonshot (${}), Sell Jupiter (${})", moonshot_price, jupiter_price);
    } else if gmgn_price > jupiter_price + fee {
        msg!("Buy Jupiter (${}), Sell GMGN (${})", jupiter_price, gmgn_price);
    } else if jupiter_price > gmgn_price + fee {
        msg!("Buy GMGN (${}), Sell Jupiter (${})", gmgn_price, jupiter_price);
    } else if moonshot_price > gmgn_price + fee {
        msg!("Buy GMGN (${}), Sell Moonshot (${})", gmgn_price, moonshot_price);
    } else if gmgn_price > moonshot_price + fee {
        msg!("Buy Moonshot (${}), Sell GMGN (${})", moonshot_price, gmgn_price);
    } else {
        msg!("No profitable arbitrage opportunity");
    }

    Ok(())
}
