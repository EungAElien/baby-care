/**
 * @file ui:alert-dialog
 * @requires @seed-design/react@^2.0.0
 * @requires @seed-design/css@^2.0.0
 **/

"use client";

import { Dialog } from "@seed-design/react";
import { forwardRef } from "react";
import { ActionButton, type ActionButtonProps } from "./action-button";
import type * as React from "react";

export interface AlertDialogRootProps extends Dialog.RootProps {
  /**
   * @default "alertdialog"
   */
  role?: Dialog.RootProps["role"];
  /**
   * @default false
   */
  closeOnInteractOutside?: Dialog.RootProps["closeOnInteractOutside"];
}

/**
 * @see https://seed-design.io/react/components/alert-dialog
 */
export const AlertDialogRoot = ({
  children,
  ...otherProps
}: AlertDialogRootProps) => {
  return (
    <Dialog.Root
      role="alertdialog"
      closeOnInteractOutside={false}
      unmountOnExit
      {...otherProps}
    >
      {children}
    </Dialog.Root>
  );
};
AlertDialogRoot.displayName = "AlertDialogRoot";

export interface AlertDialogContentProps extends Dialog.ContentProps {
  layerIndex?: number;
}

export const AlertDialogContent = forwardRef<
  HTMLDivElement,
  AlertDialogContentProps
>(({ children, layerIndex = 30, ...otherProps }, ref) => {
  return (
    <Dialog.Positioner
      style={{ "--layer-index": layerIndex } as React.CSSProperties}
    >
      <Dialog.Backdrop />
      <Dialog.Content ref={ref} {...otherProps}>
        {children}
      </Dialog.Content>
    </Dialog.Positioner>
  );
});

export type AlertDialogTriggerProps = Dialog.TriggerProps;

export const AlertDialogTrigger = Dialog.Trigger;

AlertDialogContent.displayName = "AlertDialogContent";

export type AlertDialogHeaderProps = Dialog.HeaderProps;

export const AlertDialogHeader = Dialog.Header;

export type AlertDialogTitleProps = Dialog.TitleProps;

export const AlertDialogTitle = Dialog.Title;

export type AlertDialogDescriptionProps = Dialog.DescriptionProps;

export const AlertDialogDescription = Dialog.Description;

export type AlertDialogFooterProps = Dialog.FooterProps;

export const AlertDialogFooter = Dialog.Footer;

export interface AlertDialogActionProps
  extends Omit<Dialog.ActionProps, "color">,
    ActionButtonProps {}

export const AlertDialogAction = forwardRef<
  HTMLButtonElement,
  AlertDialogActionProps
>((props, ref) => {
  return (
    <Dialog.Action asChild>
      <ActionButton {...props} ref={ref} />
    </Dialog.Action>
  );
});
AlertDialogAction.displayName = "AlertDialogAction";

/**
 * This file is a snippet from SEED Design, helping you get started quickly with @seed-design/* packages.
 * You can extend this snippet however you want.
 */
