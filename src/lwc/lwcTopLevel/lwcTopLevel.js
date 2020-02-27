
import { LightningElement } from 'lwc';

export default class LwcTopLevel extends LightningElement {

    handleBottomIsCallingEvent(event) {
        event.stopPropagation();
        this.addRedClass('c-lwc-nested-in-the-middle');
    }

    handleLightningCard(event) {
        this.addRedClass('lightning-card');
    }

    addRedClass(element) {
        this.template.querySelector(element).classList.add('red');
    }
}