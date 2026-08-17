#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Print Cost Dashboard Installer

Refactored to use modular installer package.
"""
from installer.utils import println, load_state, save_state, prompt_choice, prompt_yes_no
from installer.setup import master_setup, install_client_local, install_client_remote
from installer.utils import (
    get_client_registry,
    DATA_DIR,
    STATE_FILE,
    DEFAULT_PORT,
    DEFAULT_SERVICE_NAME,
    uninstall_master,
    uninstall_client_local,
    uninstall_client_remote,
    update_client_local,
    update_client_remote,
)
import os
import sys

# Import remaining functions from original install.py for menus
# (These are complex menu functions that we'll keep as-is for now)
sys.path.insert(0, os.path.dirname(__file__))


def list_registered_clients():
    """Show all registered clients."""
    clients = get_client_registry()
    if not clients:
        println("No registered clients found.")
        return
    
    println("\n=== Registered Clients ===")
    for i, client in enumerate(clients, 1):
        println(f"\n{i}. {client.get('printer_name', 'Unknown')}")
        println(f"   Type: {client.get('type', 'unknown')}")
        if client.get('type') == 'local':
            println(f"   Config dir: {client.get('cfg_dir', 'N/A')}")
            println(f"   Script: {client.get('script_path', 'N/A')}")
        elif client.get('type') == 'remote':
            println(f"   Remote host: {client.get('host', 'N/A')}")
            println(f"   Remote config: {client.get('config_dir', 'N/A')}")


def show_current_settings():
    """Display current installer settings."""
    println("\n=== Current Settings ===")
    println(f"Master URL: {load_state('master_url', 'Not set')}")
    println(f"Master Host: {load_state('master_host', 'Not set')}")
    println(f"Master Port: {load_state('master_port', DEFAULT_PORT)}")
    println(f"Service Name: {load_state('master_service_name', DEFAULT_SERVICE_NAME)}")
    println(f"Storage Backend: {load_state('master_storage_backend', 'csv')}")
    println(f"Reports Backend: {load_state('master_reports_backend', 'csv')}")
    println(f"API Key: {load_state('api_key', 'Not set')}")
    println(f"Printer Dir: {load_state('printer_dir', 'Not set')}")
    println(f"Script Path: {load_state('script_path', 'Not set')}")


def master_install_menu():
    """Master installation menu."""
    println("\n=== Master Installation ===")
    println("  1) Install MASTER only")
    println("  2) Install MASTER + CLIENT on this machine")
    println("  3) Back")
    choice = prompt_choice("Select option [1-3]: ", range(1, 4))

    if choice == 1:
        master_setup(master_and_client=False)
    elif choice == 2:
        master_setup(master_and_client=True)
    elif choice == 3:
        return


def client_install_menu():
    """Client installation menu."""
    while True:
        println("\n=== Client Installation ===")
        println("  1) Install CLIENT on THIS machine (local)")
        println("  2) Install CLIENT on REMOTE machine via SSH")
        println("  3) Back")
        choice = prompt_choice("Select option [1-3]: ", range(1, 4), cancel_inputs=["b"])

        if choice == 1:
            install_client_local()
            continue
        elif choice == 2:
            install_client_remote()
            continue
        elif choice in (3, None):
            return


def uninstall_main_menu():
    """Uninstall / update menu."""
    def pick_client(client_type: str):
        clients = [c for c in get_client_registry() if c.get("type") == client_type]
        if not clients:
            println(f"No registered {client_type} clients found.")
            return None
        println(f"\nRegistered {client_type} clients:")
        for i, c in enumerate(clients, 1):
            desc = c.get("printer_name", "Unknown")
            extra = c.get("cfg_dir") if client_type == "local" else c.get("host")
            println(f"  {i}) {desc} ({extra})")
        choice = prompt_choice(
            f"Select [1-{len(clients)}] (or Enter to cancel): ",
            range(1, len(clients) + 1),
            allow_empty=True,
        )
        if choice:
            return clients[choice - 1].get("printer_name")
        return None

    while True:
        println("\n=== Uninstall / Update ===")
        println("  1) Uninstall MASTER on THIS machine")
        println("  2) Uninstall LOCAL client")
        println("  3) Uninstall REMOTE client")
        println("  4) Update LOCAL client")
        println("  5) Update REMOTE client")
        println("  6) Back")
        choice = prompt_choice("Select option [1-6]: ", range(1, 7), cancel_inputs=["b"])

        if choice == 1:
            uninstall_master()
        elif choice == 2:
            pname = pick_client("local")
            if pname:
                uninstall_client_local(pname)
        elif choice == 3:
            pname = pick_client("remote")
            if pname:
                uninstall_client_remote(pname)
        elif choice == 4:
            pname = pick_client("local")
            if pname:
                update_client_local(pname)
        elif choice == 5:
            pname = pick_client("remote")
            if pname:
                update_client_remote(pname)
        elif choice in (6, None):
            return


def settings_menu():
    """Settings menu."""
    while True:
        println("\n=== Settings Menu ===")
        println("  1) Show current settings")
        println("  2) List registered clients")
        println("  3) Reset installer state")
        println("  4) Back")
        choice = prompt_choice("Select option [1-4]: ", range(1, 5), cancel_inputs=["b"])

        if choice == 1:
            show_current_settings()
        elif choice == 2:
            list_registered_clients()
        elif choice == 3:
            reset_install_state()
        elif choice in (4, None):
            return


def reset_install_state():
    """Reset installer state."""
    println("\n=== Reset Installer State ===")
    println("This will clear all remembered paths, URLs, and API keys.")
    println("It will NOT delete your data/print_costs.csv or data/settings.json.")
    confirm = prompt_yes_no("Are you sure?", default=False)
    if confirm:
        if os.path.exists(STATE_FILE):
            try:
                os.remove(STATE_FILE)
                println("Installer state reset.")
            except Exception as e:
                println(f"Failed to reset state: {e}")
        else:
            println("State file does not exist.")
    else:
        println("Cancelled.")


def main():
    """Main installer menu."""
    println("=== Print Cost Dashboard Installer ===")
    println("Note: For most questions, you can just press Enter to accept the default shown.\n")
    
    while True:
        println("\nMain Menu")
        println("  1) Install MASTER on THIS machine (dashboard)")
        println("  2) Install CLIENT (local or remote)")
        println("  3) Uninstall (master or client)")
        println("  4) Settings")
        println("  5) Exit")
        choice = prompt_choice("Select option [1-5]: ", range(1, 6), cancel_inputs=["b"])

        if choice == 1:
            master_install_menu()
        elif choice == 2:
            client_install_menu()
        elif choice == 3:
            uninstall_main_menu()
        elif choice == 4:
            settings_menu()
        elif choice in (5, None):
            println("Goodbye.")
            break


if __name__ == "__main__":
    main()
