/**
 * Created by vsiukaiev on 24-Feb-20.
 */

import { LightningElement } from 'lwc';

export default class LwcNestedInTheMiddle extends LightningElement {

    handleEvent(event) {
        this.addGreenClass('c-lwc-nested-at-the-end');
    }

    handleLightningCard(event) {
        this.addGreenClass('lightning-card');
    }

    addGreenClass(element) {
        this.template.querySelector(element).classList.add('green');
    }
}